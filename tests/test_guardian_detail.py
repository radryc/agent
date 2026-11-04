import asyncio
import json
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.agent import tools  # noqa: E402


def _payload() -> dict:
    """Small synthetic PartitionDetailResponse with a large tail.

    The real goalert detail is ~75 KB pretty-printed. This fixture keeps the
    interesting shape while padding the k8s intent past the old 8 KB cap.
    """
    hints = [{"title": f"hint-{idx}", "description": "x" * 200} for idx in range(40)]
    return {
        "generatedAt": "2026-11-04T10:59:01Z",
        "partition": {
            "manifest": {
                "apiVersion": "guardian/v1alpha1",
                "kind": "Partition",
                "metadata": {"name": "goalert"},
            }
        },
        "health": {
            "status": "Healthy",
            "displayStatus": "Healthy",
            "summary": "8 assets match desired state.",
            "healthy": 8,
            "attention": 0,
            "failing": 0,
            "pending": 0,
            "locked": 0,
        },
        "intents": [
            {
                "name": "images",
                "status": "Healthy",
                "displayStatus": "No diff",
                "health": "healthy",
                "summary": "Desired and live state match",
                "locked": False,
                "assets": [
                    {
                        "name": "lb-build",
                        "type": "ImageBuild",
                        "status": "Healthy",
                        "ports": [],
                        "replicas": 1,
                    }
                ],
            },
            {
                "name": "k8s",
                "status": "Healthy",
                "displayStatus": "No diff",
                "health": "healthy",
                "summary": "Desired and live state match",
                "targetSummary": "k8s-main / goalert",
                "locked": False,
                "manifestVersionID": "ver_1",
                "manifest": {
                    "apiVersion": "guardian/v1alpha1",
                    "kind": "Intent",
                    "metadata": {"name": "k8s"},
                    "spec": {
                        "assets": [
                            {
                                "type": "Compute",
                                "name": "goalert",
                                "version": "20261104-1040",
                                "properties": {
                                    "image": "goalert/goalert:v0.34.1",
                                    "env": {"GOALERT_LISTEN": ":8081"},
                                    "ports": [
                                        {
                                            "containerPort": 8081,
                                            "name": "http",
                                            "protocol": "TCP",
                                            "servicePort": 8081,
                                        }
                                    ],
                                },
                            },
                            {
                                "type": "LoadBalancer",
                                "name": "goalert-edge",
                                "version": "20261104-1040",
                                "dependsOn": ["goalert"],
                                "properties": {
                                    "image": "registry.strata.local:5000/lb:test",
                                    "listeners": [
                                        {
                                            "description": "GoAlert UI/API",
                                            "name": "http",
                                            "port": 8081,
                                            "protocol": "TCP",
                                        }
                                    ],
                                    "serviceType": "LoadBalancer",
                                    "targets": ["goalert"],
                                },
                            },
                        ]
                    },
                },
                "assets": [
                    {
                        "name": "goalert",
                        "type": "Compute",
                        "status": "Healthy",
                        "health": "healthy",
                        "summary": "Matches desired state",
                        "ports": ["http:8081"],
                        "replicas": 1,
                        "properties": {"image": "goalert/goalert:v0.34.1"},
                        "hints": hints,
                    },
                    {
                        "name": "goalert-edge",
                        "type": "LoadBalancer",
                        "status": "Healthy",
                        "health": "healthy",
                        "summary": "Matches desired state",
                        "ports": ["http:8081"],
                        "replicas": 1,
                        "properties": {
                            "listeners": [
                                {
                                    "name": "http",
                                    "port": 8081,
                                    "protocol": "TCP",
                                }
                            ],
                            "serviceType": "LoadBalancer",
                            "targets": ["goalert"],
                        },
                        "outputs": {"address": "goalert-edge.goalert.svc:8081"},
                        "hints": hints,
                    },
                ],
            },
        ],
        "topology": {"nodes": [], "edges": []},
        "recentEvents": [],
    }


class GuardianDetailProjectionTest(unittest.TestCase):
    def test_truncate_reports_byte_counts(self):
        result = tools._truncate("a" * 9000, max_len=8000)
        self.assertIn("8000 of 9000 chars shown", result)
        self.assertIn("1000 omitted", result)

    def test_summary_is_small_and_lists_asset_ports(self):
        summary = tools._guardian_partition_summary(_payload())
        text = json.dumps(summary, indent=2)
        self.assertLess(len(text), 8000)
        self.assertIn("goalert-edge", text)
        self.assertIn("http:8081", text)
        self.assertNotIn("listeners", text)
        self.assertNotIn("hints", text)

    def test_manifest_view_selects_one_intent_and_asset(self):
        view = tools._guardian_intent_manifest_view(
            _payload(), "k8s", asset_name="goalert-edge"
        )
        self.assertIsNotNone(view)
        manifest_assets = view["manifest"]["spec"]["assets"]
        self.assertEqual([asset["name"] for asset in manifest_assets], ["goalert-edge"])
        self.assertEqual(
            manifest_assets[0]["properties"]["listeners"][0]["port"], 8081
        )
        self.assertEqual([asset["name"] for asset in view["assets"]], ["goalert-edge"])
        text = json.dumps(view, indent=2)
        self.assertLess(len(text), 8000)
        self.assertNotIn("goalert/goalert", text)

    def test_manifest_view_returns_none_for_missing_asset(self):
        view = tools._guardian_intent_manifest_view(
            _payload(), "k8s", asset_name="does-not-exist"
        )
        self.assertIsNone(view)


class GuardianDetailHandlerTest(unittest.TestCase):
    def setUp(self):
        self.original = tools._guardian_get

        async def fake_get(_path: str) -> str:
            return json.dumps(_payload())

        tools._guardian_get = fake_get

    def tearDown(self):
        tools._guardian_get = self.original

    def test_default_is_summary_without_manifests(self):
        result = asyncio.run(
            tools._tool_guardian_partition_detail({"partition": "goalert"})
        )
        self.assertIn("goalert-edge", result)
        self.assertIn("http:8081", result)
        self.assertNotIn("serviceType", result)

    def test_intent_defaults_to_manifest(self):
        result = asyncio.run(
            tools._tool_guardian_partition_detail(
                {"partition": "goalert", "intent": "k8s"}
            )
        )
        self.assertIn("goalert-edge", result)
        self.assertIn("serviceType", result)
        self.assertIn("8081", result)
        self.assertLess(len(result), 24000)

    def test_unknown_intent_lists_available_intents(self):
        result = asyncio.run(
            tools._tool_guardian_partition_detail(
                {"partition": "goalert", "intent": "missing"}
            )
        )
        self.assertIn("not found", result)
        self.assertIn("images, k8s", result)

    def test_unknown_asset_lists_available_assets(self):
        result = asyncio.run(
            tools._tool_guardian_partition_detail(
                {
                    "partition": "goalert",
                    "intent": "k8s",
                    "asset": "missing",
                }
            )
        )
        self.assertIn("not found", result)
        self.assertIn("goalert, goalert-edge", result)

    def test_asset_requires_intent(self):
        result = asyncio.run(
            tools._tool_guardian_partition_detail(
                {"partition": "goalert", "asset": "goalert-edge"}
            )
        )
        self.assertIn("asset requires intent", result)

    def test_manifest_requires_intent(self):
        result = asyncio.run(
            tools._tool_guardian_partition_detail(
                {"partition": "goalert", "include": "manifest"}
            )
        )
        self.assertIn("include=manifest requires intent", result)

    def test_invalid_include_is_rejected(self):
        result = asyncio.run(
            tools._tool_guardian_partition_detail(
                {"partition": "goalert", "include": "everything"}
            )
        )
        self.assertIn("include must be one of", result)

    def test_full_view_keeps_large_payload_below_cap(self):
        result = asyncio.run(
            tools._tool_guardian_partition_detail(
                {"partition": "goalert", "include": "full"}
            )
        )
        self.assertIn("hints", result)
        self.assertNotIn("[truncated:", result)
        self.assertLess(len(result), tools._GUARDIAN_DETAIL_FULL_MAX)


if __name__ == "__main__":
    unittest.main()
