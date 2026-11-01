import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.api.nightwatch import router as nightwatch_proxy_router  # noqa: E402
from app.config import settings  # noqa: E402
from app.nightwatch.api import configure, router  # noqa: E402
from app.nightwatch.guardian import enabled_partitions  # noqa: E402
from app.nightwatch.investigator import (  # noqa: E402
    Candidate,
    EvidenceDraft,
    InvestigationResult,
    Investigator,
)
from app.nightwatch.sentinel import correlation_key, normalize_alert  # noqa: E402
from app.nightwatch.store import NightwatchStore  # noqa: E402
from app.nightwatch.worker import NightwatchWorker  # noqa: E402


class AssignmentTest(unittest.TestCase):
    def test_enabled_partitions_only_truthy_label(self):
        partitions = [
            {"name": "a", "labels": {"nightwatch.strata.io/enabled": "true"}},
            {"name": "b", "labels": {"nightwatch.strata.io/enabled": "false"}},
            {"name": "c", "labels": {}},
            {"name": "d", "labels": {"nightwatch.strata.io/enabled": "YES"}},
        ]
        self.assertEqual([p["name"] for p in enabled_partitions(partitions, "nightwatch.strata.io/enabled")], ["a", "d"])


class SentinelTest(unittest.TestCase):
    def test_normalize_alert_derives_service_and_partition(self):
        alert = normalize_alert(
            {
                "fingerprint": "fp-1",
                "labels": {
                    "alertname": "HighErrorRate",
                    "partition": "div-a-orders",
                    "guardian_intent": "orders",
                    "guardian_asset": "api",
                    "severity": "critical",
                },
                "annotations": {"summary": "order errors"},
                "status": "firing",
                "startsAt": "2026-10-31T02:13:00Z",
            }
        )
        self.assertEqual(alert["fingerprint"], "fp-1")
        self.assertEqual(alert["partition"], "div-a-orders")
        self.assertEqual(alert["service"], "div-a-orders.orders.api")
        self.assertEqual(alert["severity"], "critical")
        self.assertEqual(alert["annotations"]["summary"], "order errors")
        self.assertEqual(correlation_key(alert), "div-a-orders/div-a-orders.orders.api")

    def test_correlation_key_falls_back_to_alertname(self):
        alert = normalize_alert({"labels": {"alertname": "ProbeDown"}, "partition": "p"})
        self.assertEqual(correlation_key(alert), "p/ProbeDown")

    def test_target_labels_identify_cross_partition_component(self):
        alert = normalize_alert(
            {
                "labels": {
                    "alertname": "DependencyLatency",
                    "partition": "div-a-orders",
                    "guardian_target_partition": "div-a-billing",
                    "guardian_target_intent": "payments",
                    "guardian_target_asset": "api",
                }
            }
        )
        self.assertEqual(alert["target_partition"], "div-a-billing")
        self.assertEqual(alert["target_service"], "div-a-billing.payments.api")


class StoreTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.store = NightwatchStore(f"sqlite+aiosqlite:///{self.tmp.name}")
        await self.store.init()

    async def asyncTearDown(self):
        await self.store.close()
        Path(self.tmp.name).unlink(missing_ok=True)

    async def test_event_hash_chain(self):
        incident = await self.store.create_incident(
            partition="p",
            correlation_key="p/svc",
            title="HighErrorRate · p.svc",
            severity="critical",
        )
        await self.store.append_event(
            incident_id=incident["id"],
            type="alert.firing",
            summary="alert fired",
            payload={"a": 1},
        )
        await self.store.append_event(
            incident_id=incident["id"],
            type="investigation.started",
            summary="investigation started",
        )
        events = await self.store.list_events(incident["id"])
        self.assertEqual([e["seq"] for e in events], [1, 2, 3])
        self.assertEqual(events[0]["prev_hash"], "")
        self.assertEqual(events[1]["prev_hash"], events[0]["hash"])
        self.assertEqual(events[2]["prev_hash"], events[1]["hash"])
        self.assertNotEqual(events[1]["hash"], events[2]["hash"])

    async def test_incident_alert_and_resolution(self):
        incident = await self.store.create_incident(
            partition="p",
            correlation_key="p/svc",
            title="HighErrorRate",
            severity="warning",
        )
        await self.store.upsert_alert(
            incident["id"],
            {
                "fingerprint": "fp-1",
                "alertname": "HighErrorRate",
                "status": "firing",
                "severity": "warning",
                "starts_at": datetime.utcnow(),
                "labels": {"alertname": "HighErrorRate"},
            },
        )
        detail = await self.store.get_incident(incident["id"])
        self.assertEqual(len(detail["alerts"]), 1)
        await self.store.close_incident(incident["id"], "source resolved")
        detail = await self.store.get_incident(incident["id"])
        self.assertEqual(detail["status"], "resolved")
        self.assertIsNotNone(detail["closed_at"])

    async def test_assignment_upsert_and_disable_missing(self):
        await self.store.upsert_assignment("a", True)
        await self.store.upsert_assignment("b", True)
        await self.store.disable_missing_assignments({"a"})
        assignments = {item["partition"]: item for item in await self.store.list_assignments()}
        self.assertTrue(assignments["a"]["enabled"])
        self.assertFalse(assignments["b"]["enabled"])

    async def test_brief_counts_escalation(self):
        incident = await self.store.create_incident(
            partition="p",
            correlation_key="p/svc",
            title="HighErrorRate",
            severity="critical",
        )
        await self.store.update_incident(
            incident["id"],
            escalation_status="recommended",
            escalation_target="p",
            escalation_reason="low confidence",
            recommended_action="escalate",
        )
        brief = await self.store.brief()
        self.assertEqual(brief["counts"]["incidents"], 1)
        self.assertEqual(brief["counts"]["escalated"], 1)


class FakeGuardian:
    def __init__(self, partitions):
        self.partitions = partitions

    async def list_partitions(self):
        return self.partitions

    async def topology(self, partition):
        return {"nodes": [], "edges": []}

    async def history(self, partition, limit=20):
        return {"events": [], "deployments": []}

    async def close(self):
        pass


class FakeDoctor:
    def __init__(self, alerts):
        self._alerts = alerts

    async def alerts(self, partition):
        return self._alerts

    async def metric(self, query, at=None):
        return {"status": "success", "data": {"resultType": "vector", "result": []}}

    async def logs(self, partition, **kwargs):
        return {"logs": []}

    async def recent_traces(self, partition, **kwargs):
        return {"traces": []}

    async def guardian_topology(self, partition=None):
        return {"nodes": [], "edges": []}

    async def guardian_events(self, partition, limit=20):
        return {"events": []}

    async def close(self):
        pass


class FakeInvestigator:
    def __init__(self, result):
        self.result = result
        self.calls = 0

    async def investigate(self, incident, alerts, *, evidence_prefix="ev"):
        self.calls += 1
        return self.result


class FakeAssessor:
    def __init__(self, payload):
        self.payload = payload

    async def __call__(self, pack):
        return self.payload


class FleetMetricDoctor(FakeDoctor):
    async def metric(self, query, at=None):
        if "guardian_partition!=" in query:
            return {
                "status": "success",
                "data": {
                    "resultType": "vector",
                    "result": [
                        {
                            "metric": {
                                "guardian_partition": "div-a-billing",
                                "service": "div-a-billing.payments-api",
                            },
                            "value": [0, "0"],
                        }
                    ],
                },
            }
        return await super().metric(query, at=at)


class InvestigatorEscalationTest(unittest.IsolatedAsyncioTestCase):
    async def test_fleet_metric_anomaly_becomes_cross_partition_candidate(self):
        investigator = Investigator(FakeGuardian([]), FleetMetricDoctor([]))
        candidates = await investigator._collect_fleet_anomalies(
            "div-a-orders", datetime.utcnow(), [], []
        )
        self.assertTrue(candidates)
        self.assertEqual(candidates[0].partition, "div-a-billing")
        self.assertEqual(candidates[0].service, "div-a-billing.payments-api")

    async def test_cross_partition_escalates_and_recommends_handoff(self):
        investigator = Investigator(
            FakeGuardian([]),
            FakeDoctor([]),
            assessor=FakeAssessor(
                {
                    "summary": "billing is the likely cause",
                    "root_cause_candidate": {
                        "partition": "div-a-billing",
                        "service": "div-a-billing.payments-api",
                        "confidence": 0.86,
                    },
                    "cross_partition": True,
                    "recommended_action": "rollback",
                    "recommended_target": "div-a-billing.payments-api",
                    "rationale": "billing latency preceded orders errors",
                    "evidence_ids": ["pending-0"],
                }
            ),
        )
        result = await investigator.investigate(
            {"id": "inc-1", "partition": "div-a-orders", "title": "orders errors", "severity": "critical"},
            [{"alertname": "HighErrorRate", "service": "div-a-orders.orders-api", "labels": {}, "annotations": {}}],
        )
        self.assertTrue(result.cross_partition)
        self.assertTrue(result.escalate)
        self.assertEqual(result.recommended_action, "handoff")
        self.assertEqual(result.escalation_target, "div-a-billing")
        self.assertIn("outside", result.escalation_reason)

    async def test_low_confidence_escalates(self):
        investigator = Investigator(
            FakeGuardian([]),
            FakeDoctor([]),
            assessor=FakeAssessor(
                {
                    "summary": "not sure",
                    "root_cause_candidate": {
                        "partition": "div-a-orders",
                        "service": "div-a-orders.orders-api",
                        "confidence": 0.2,
                    },
                    "cross_partition": False,
                    "recommended_action": "restart",
                    "rationale": "maybe",
                    "evidence_ids": [],
                }
            ),
        )
        result = await investigator.investigate(
            {"id": "inc-1", "partition": "div-a-orders", "title": "orders errors", "severity": "critical"},
            [{"alertname": "HighErrorRate", "service": "div-a-orders.orders-api", "labels": {}, "annotations": {}}],
        )
        self.assertTrue(result.escalate)
        self.assertEqual(result.recommended_action, "escalate")
        self.assertIn("below escalation threshold", result.escalation_reason)


class WorkerTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.store = NightwatchStore(f"sqlite+aiosqlite:///{self.tmp.name}")
        await self.store.init()

    async def asyncTearDown(self):
        await self.store.close()
        Path(self.tmp.name).unlink(missing_ok=True)

    async def test_worker_creates_incident_and_escalation_event(self):
        guardian = FakeGuardian(
            [{"name": "div-a-orders", "labels": {"nightwatch.strata.io/enabled": "true"}}]
        )
        doctor = FakeDoctor(
            [
                {
                    "fingerprint": "fp-1",
                    "partition": "div-a-orders",
                    "labels": {
                        "alertname": "HighErrorRate",
                        "service": "div-a-orders.orders-api",
                        "severity": "critical",
                    },
                    "annotations": {"summary": "orders errors"},
                    "status": "firing",
                    "severity": "critical",
                }
            ]
        )
        result = InvestigationResult(
            summary="billing likely cause",
            root_cause_partition="div-a-billing",
            root_cause_service="div-a-billing.payments-api",
            confidence=0.8,
            recommended_action="handoff",
            recommendation_target="div-a-billing.payments-api",
            rationale="billing latency preceded orders errors",
            cross_partition=True,
            escalate=True,
            escalation_target="div-a-billing",
            escalation_reason="outside assignment",
            evidence=[
                EvidenceDraft(
                    kind="topology",
                    query="topology",
                    from_ts=None,
                    to_ts=None,
                    result_summary="1 node",
                    result_hash="abc",
                    source="guardian",
                )
            ],
            candidates=[
                Candidate(
                    partition="div-a-billing",
                    service="div-a-billing.payments-api",
                    score=0.8,
                    confidence=0.8,
                    rationale="dependency",
                )
            ],
        )
        investigator = FakeInvestigator(result)
        worker = NightwatchWorker(self.store, guardian, doctor, investigator)
        await worker.run_once()
        incidents = await self.store.list_incidents(partition="div-a-orders")
        self.assertEqual(len(incidents), 1)
        detail = await self.store.get_incident(incidents[0]["id"])
        self.assertEqual(detail["handoff_status"], "handed_off")
        self.assertEqual(detail["escalation_status"], "recommended")
        types = [event["type"] for event in detail["events"]]
        self.assertIn("finding.cross_partition", types)
        self.assertIn("escalation.recommended", types)
        self.assertGreaterEqual(investigator.calls, 1)

        # Polling the same alert again must not create a duplicate incident.
        await worker.run_once()
        incidents = await self.store.list_incidents(partition="div-a-orders")
        self.assertEqual(len(incidents), 1)


class ProxyTest(unittest.TestCase):
    def setUp(self):
        self._original = settings.nightwatch_url
        settings.nightwatch_url = ""
        app = FastAPI()
        app.include_router(nightwatch_proxy_router)
        self.client = TestClient(app)

    def tearDown(self):
        settings.nightwatch_url = self._original

    def test_overview_returns_safe_empty_state(self):
        response = self.client.get("/api/nightwatch/overview")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertFalse(body["available"])
        self.assertEqual(body["mode"], "shadow")

    def test_incidents_returns_empty_list(self):
        response = self.client.get("/api/nightwatch/incidents")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["incidents"], [])

    def test_brief_returns_zero_counts(self):
        response = self.client.get("/api/nightwatch/brief")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["counts"]["incidents"], 0)


class ApiTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.store = NightwatchStore(f"sqlite+aiosqlite:///{self.tmp.name}")
        await self.store.init()
        configure(self.store)
        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app)

    async def asyncTearDown(self):
        await self.store.close()
        Path(self.tmp.name).unlink(missing_ok=True)

    async def test_health_and_empty_incidents(self):
        health = self.client.get("/v1/health")
        self.assertEqual(health.status_code, 200)
        self.assertTrue(health.json()["store_ready"])
        incidents = self.client.get("/v1/incidents")
        self.assertEqual(incidents.status_code, 200)
        self.assertEqual(incidents.json()["incidents"], [])

    async def test_brief_returns_counts(self):
        await self.store.create_incident(
            partition="p",
            correlation_key="p/svc",
            title="HighErrorRate",
            severity="warning",
        )
        response = self.client.get("/v1/brief")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["counts"]["incidents"], 1)


if __name__ == "__main__":
    unittest.main()
