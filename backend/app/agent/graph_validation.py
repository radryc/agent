"""Shared validation for Doctor graph and dashboard files.

Used by the native ``doctor_graph_validate`` agent tool and by the
``create-doctor-graphs`` skill's command-line validator.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover - YAML is optional for JSON-only users
    yaml = None

WIDGET_KINDS = {
    "timeseries",
    "stat",
    "toplist",
    "gauge",
    "table",
    "logs",
    "traces",
    "topology",
    "markdown",
}
DATASOURCES = {"prometheus", "loki", "tempo", "native_logs", "native_traces"}
DISPLAY_MODES = {"line", "area", "bars"}
UNITS = {"none", "short", "percent", "seconds", "ms", "bytes", "bps", "reqps"}
COLORS = {"green", "amber", "red"}
VARIABLE_TYPES = {"label_values", "custom", "text", "partition", "service"}
PREFERRED_RANGES = {15, 60, 360, 1440}
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


class Report:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def error(self, message: str) -> None:
        self.errors.append(message)

    def warn(self, message: str) -> None:
        self.warnings.append(message)


def parse_text(text: str, fmt: str = "") -> Any:
    """Parse JSON or YAML text. ``fmt`` may be json, yaml, or empty (sniff)."""
    normalized = fmt.strip().lower().lstrip(".")
    if normalized == "json" or (
        not normalized and text.lstrip().startswith(("{", "["))
    ):
        return json.loads(text)
    if normalized in {"yaml", "yml"}:
        if yaml is None:
            raise RuntimeError("PyYAML is not installed; install it or pass JSON")
        return yaml.safe_load(text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        if yaml is None:
            raise RuntimeError("file is not JSON and PyYAML is not installed")
        return yaml.safe_load(text)


def load_document(path: Path) -> Any:
    text = path.read_text(encoding="utf-8")
    suffix = path.suffix.lower().lstrip(".")
    return parse_text(text, suffix)


def is_nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def validate_partitions(value: Any, where: str, report: Report) -> None:
    if value is None:
        report.error(f"{where}: partitions is required")
        return
    if not isinstance(value, list):
        report.error(f"{where}: partitions must be an array of partition names")
        return
    seen: set[str] = set()
    names: list[str] = []
    for index, entry in enumerate(value):
        if not isinstance(entry, str) or not entry.strip():
            report.error(f"{where}[{index}]: partition must be a non-empty string")
            continue
        name = entry.strip()
        if name in seen:
            report.error(f"{where}: duplicate partition {name!r}")
            continue
        seen.add(name)
        names.append(name)
    if not names:
        report.error(f"{where}: partitions must list at least one partition")
    if "*" in seen and len(names) > 1:
        report.warn(f"{where}: '*' already means every partition; extra entries are redundant")


def validate_saved_graph(graph: Any, where: str, report: Report) -> None:
    if not isinstance(graph, dict):
        report.error(f"{where}: expected an object")
        return
    if not is_nonempty_string(graph.get("title")):
        report.error(f"{where}: missing non-empty title")
    if not is_nonempty_string(graph.get("query")):
        report.error(f"{where}: missing non-empty query")
    validate_partitions(graph.get("partitions"), f"{where}.partitions", report)

    if "id" in graph and graph["id"] is not None and not is_nonempty_string(graph["id"]):
        report.error(f"{where}: id must be a non-empty string when present")

    range_minutes = graph.get("rangeMinutes", 60)
    if not is_number(range_minutes) or range_minutes <= 0:
        report.error(f"{where}: rangeMinutes must be a positive number")
    elif int(range_minutes) not in PREFERRED_RANGES:
        report.warn(
            f"{where}: rangeMinutes={range_minutes} is valid but the UI options are "
            f"{sorted(PREFERRED_RANGES)}"
        )

    refresh = graph.get("refreshIntervalSec", 0)
    if not is_number(refresh) or refresh < 0:
        report.error(f"{where}: refreshIntervalSec must be zero or a positive number")
    elif 0 < refresh < 5:
        report.warn(f"{where}: refreshIntervalSec={refresh} is clamped to 5s by the UI")


def validate_saved_dashboard(dashboard: Any, where: str, report: Report) -> None:
    if not isinstance(dashboard, dict):
        report.error(f"{where}: expected an object")
        return
    if not is_nonempty_string(dashboard.get("name")):
        report.error(f"{where}: missing non-empty name")
    validate_partitions(dashboard.get("partitions"), f"{where}.partitions", report)
    graphs = dashboard.get("graphs")
    if not isinstance(graphs, list):
        report.error(f"{where}: graphs must be an array")
        return
    if not graphs:
        report.warn(f"{where}: dashboard has no graphs")
    for index, graph in enumerate(graphs):
        validate_saved_graph(graph, f"{where}.graphs[{index}]", report)


def validate_saved_bundle(document: dict, report: Report) -> str:
    version = document.get("version")
    if version != 1:
        report.error(f"version must be 1 for a Saved Graph Bundle, got {version!r}")
    if not isinstance(document.get("savedGraphs"), list):
        report.error("savedGraphs must be an array (use [] when there are none)")
    if not isinstance(document.get("savedDashboards"), list):
        report.error("savedDashboards must be an array (use [] when there are none)")

    graphs = document.get("savedGraphs") or []
    dashboards = document.get("savedDashboards") or []
    if isinstance(graphs, list) and isinstance(dashboards, list) and not graphs and not dashboards:
        report.error("bundle contains no savedGraphs or savedDashboards")

    graph_ids: set[str] = set()
    for index, graph in enumerate(graphs if isinstance(graphs, list) else []):
        where = f"savedGraphs[{index}]"
        validate_saved_graph(graph, where, report)
        if isinstance(graph, dict) and is_nonempty_string(graph.get("id")):
            graph_id = graph["id"]
            if graph_id in graph_ids:
                report.error(f"{where}: duplicate graph id {graph_id!r}")
            graph_ids.add(graph_id)

    for index, dashboard in enumerate(dashboards if isinstance(dashboards, list) else []):
        where = f"savedDashboards[{index}]"
        validate_saved_dashboard(dashboard, where, report)
        if isinstance(dashboard, dict) and is_nonempty_string(dashboard.get("id")):
            dashboard_id = dashboard["id"]
            if dashboard_id in graph_ids:
                report.error(f"{where}: duplicate id {dashboard_id!r}")
            graph_ids.add(dashboard_id)

    return "saved-graph-bundle"


def validate_variables(variables: Any, where: str, report: Report) -> None:
    if variables is None:
        return
    if not isinstance(variables, list):
        report.error(f"{where}: variables must be an array")
        return
    names: set[str] = set()
    for index, variable in enumerate(variables):
        vw = f"{where}[{index}]"
        if not isinstance(variable, dict):
            report.error(f"{vw}: expected an object")
            continue
        name = variable.get("name")
        if not is_nonempty_string(name):
            report.error(f"{vw}: missing non-empty name")
        elif name in names:
            report.error(f"{vw}: duplicate variable name {name!r}")
        else:
            names.add(name)
        if not is_nonempty_string(variable.get("label")):
            report.error(f"{vw}: missing non-empty label")
        var_type = variable.get("type")
        if var_type not in VARIABLE_TYPES:
            report.error(f"{vw}: type must be one of {sorted(VARIABLE_TYPES)}")
        if var_type == "label_values" and not is_nonempty_string(variable.get("labelKey")):
            report.error(f"{vw}: label_values variables require labelKey")
        if var_type == "custom" and not isinstance(variable.get("options"), list):
            report.error(f"{vw}: custom variables require an options array")
        if not is_nonempty_string(variable.get("defaultValue")):
            report.error(f"{vw}: missing non-empty defaultValue")


def validate_grid_pos(grid: Any, where: str, report: Report) -> None:
    if not isinstance(grid, dict):
        report.error(f"{where}: gridPos must be an object with x, y, w, h")
        return
    for key in ("x", "y", "w", "h"):
        if not isinstance(grid.get(key), int) or isinstance(grid.get(key), bool):
            report.error(f"{where}: gridPos.{key} must be an integer")
    if any(not isinstance(grid.get(key), int) for key in ("x", "y", "w", "h")):
        return
    x, y, w, h = grid["x"], grid["y"], grid["w"], grid["h"]
    if not 0 <= x <= 11:
        report.error(f"{where}: gridPos.x={x} must be 0..11")
    if y < 0:
        report.error(f"{where}: gridPos.y={y} must be >= 0")
    if not 1 <= w <= 12:
        report.error(f"{where}: gridPos.w={w} must be 1..12")
    if not 1 <= h <= 24:
        report.error(f"{where}: gridPos.h={h} must be 1..24")
    if x + w > 12:
        report.error(f"{where}: gridPos.x + gridPos.w = {x + w} exceeds the 12-column grid")


def validate_query(query: Any, where: str, report: Report) -> None:
    if not isinstance(query, dict):
        report.error(f"{where}: expected an object")
        return
    if not is_nonempty_string(query.get("refId")):
        report.error(f"{where}: missing non-empty refId")
    datasource = query.get("datasource")
    if datasource not in DATASOURCES:
        report.error(f"{where}: datasource must be one of {sorted(DATASOURCES)}")
    if not is_nonempty_string(query.get("expr")):
        report.error(f"{where}: missing non-empty expr")


def validate_widget(widget: Any, where: str, report: Report) -> None:
    if not isinstance(widget, dict):
        report.error(f"{where}: expected an object")
        return
    if not is_nonempty_string(widget.get("id")):
        report.error(f"{where}: missing non-empty id")
    elif not SLUG_RE.match(widget["id"]):
        report.warn(f"{where}: id {widget['id']!r} is not a lowercase URL-safe slug")
    if not is_nonempty_string(widget.get("title")):
        report.error(f"{where}: missing non-empty title")

    kind = widget.get("kind")
    if kind not in WIDGET_KINDS:
        report.error(f"{where}: kind must be one of {sorted(WIDGET_KINDS)}")
    validate_grid_pos(widget.get("gridPos"), f"{where}.gridPos", report)
    if widget.get("partitions") is not None:
        validate_partitions(widget.get("partitions"), f"{where}.partitions", report)

    queries = widget.get("queries")
    if kind in {"markdown", "topology"}:
        if queries is not None and not isinstance(queries, list):
            report.error(f"{where}: queries must be an array when present")
    else:
        if not isinstance(queries, list) or not queries:
            report.error(f"{where}: {kind or 'widget'} requires at least one query")
            queries = []
        ref_ids: set[str] = set()
        for index, query in enumerate(queries):
            qw = f"{where}.queries[{index}]"
            validate_query(query, qw, report)
            if isinstance(query, dict) and is_nonempty_string(query.get("refId")):
                if query["refId"] in ref_ids:
                    report.error(f"{qw}: duplicate refId {query['refId']!r}")
                ref_ids.add(query["refId"])
            if isinstance(query, dict) and kind == "gauge" and query.get("datasource") != "prometheus":
                report.warn(f"{qw}: gauge widgets currently read Prometheus only")
            if isinstance(query, dict) and kind == "logs" and query.get("datasource") not in {
                "loki",
                "native_logs",
            }:
                report.warn(f"{qw}: logs widgets normally use loki or native_logs")
            if isinstance(query, dict) and kind == "traces" and query.get("datasource") not in {
                "tempo",
                "native_traces",
            }:
                report.warn(f"{qw}: traces widgets normally use tempo or native_traces")

    visualization = widget.get("visualization")
    if visualization is None:
        return
    if not isinstance(visualization, dict):
        report.error(f"{where}: visualization must be an object")
        return

    display_mode = visualization.get("displayMode")
    if display_mode is not None:
        if display_mode not in DISPLAY_MODES:
            report.error(f"{where}: visualization.displayMode must be one of {sorted(DISPLAY_MODES)}")
        if kind != "timeseries":
            report.warn(f"{where}: displayMode applies to timeseries widgets, not {kind!r}")

    unit = visualization.get("unit")
    if unit is not None and unit not in UNITS:
        report.error(f"{where}: visualization.unit must be one of {sorted(UNITS)}")

    thresholds = visualization.get("thresholds")
    if thresholds is not None:
        if not isinstance(thresholds, list):
            report.error(f"{where}: visualization.thresholds must be an array")
        else:
            for index, threshold in enumerate(thresholds):
                tw = f"{where}.visualization.thresholds[{index}]"
                if not isinstance(threshold, dict):
                    report.error(f"{tw}: expected an object")
                    continue
                if not is_number(threshold.get("value")):
                    report.error(f"{tw}: value must be a number")
                if threshold.get("color") not in COLORS:
                    report.error(f"{tw}: color must be one of {sorted(COLORS)}")

    if kind == "markdown" and not is_nonempty_string(visualization.get("markdownContent")):
        report.error(f"{where}: markdown widgets require visualization.markdownContent")


def validate_dashboard(document: dict, where: str, report: Report) -> str:
    if document.get("version") != "doctor.dashboard/v1":
        report.error(f"{where}: version must be 'doctor.dashboard/v1'")
    if not is_nonempty_string(document.get("id")):
        report.error(f"{where}: missing non-empty id")
    elif not SLUG_RE.match(document["id"]):
        report.warn(f"{where}: id {document['id']!r} is not a lowercase URL-safe slug")
    if not is_nonempty_string(document.get("title")):
        report.error(f"{where}: missing non-empty title")
    if not is_nonempty_string(document.get("tenant")):
        report.error(f"{where}: missing non-empty tenant (use 'default' for shared dashboards)")

    validate_partitions(document.get("partitions"), f"{where}.partitions", report)

    validate_variables(document.get("variables"), f"{where}.variables", report)

    layout = document.get("layout")
    if not isinstance(layout, list):
        report.error(f"{where}: layout must be an array")
        return "dashboard"
    if not layout:
        report.warn(f"{where}: layout is empty")

    widget_ids: set[str] = set()
    for index, widget in enumerate(layout):
        ww = f"{where}.layout[{index}]"
        validate_widget(widget, ww, report)
        if isinstance(widget, dict) and is_nonempty_string(widget.get("id")):
            widget_id = widget["id"]
            if widget_id in widget_ids:
                report.error(f"{ww}: duplicate widget id {widget_id!r}")
            widget_ids.add(widget_id)

    return "dashboard"


def validate_document(document: Any, report: Report) -> str:
    if isinstance(document, list):
        if not document:
            report.error("graph list is empty")
            return "saved-graph-list"
        for index, graph in enumerate(document):
            validate_saved_graph(graph, f"[{index}]", report)
        return "saved-graph-list"

    if not isinstance(document, dict):
        report.error("document root must be an object or array")
        return "unknown"

    version = document.get("version")
    if version == "doctor.dashboard.bundle/v1":
        dashboards = document.get("dashboards")
        if not isinstance(dashboards, list):
            report.error("dashboards must be an array")
            return "dashboard-bundle"
        if not dashboards:
            report.warn("dashboard bundle is empty")
        ids: set[str] = set()
        for index, dashboard in enumerate(dashboards):
            where = f"dashboards[{index}]"
            if not isinstance(dashboard, dict):
                report.error(f"{where}: expected an object")
                continue
            validate_dashboard(dashboard, where, report)
            if is_nonempty_string(dashboard.get("id")):
                dashboard_id = dashboard["id"]
                if dashboard_id in ids:
                    report.error(f"{where}: duplicate dashboard id {dashboard_id!r}")
                ids.add(dashboard_id)
        return "dashboard-bundle"

    if version == "doctor.dashboard/v1":
        return validate_dashboard(document, "$", report)

    if version == 1 or "savedGraphs" in document or "savedDashboards" in document:
        return validate_saved_bundle(document, report)

    # Shorthands accepted by the saved-graph importer.
    if "name" in document and "graphs" in document:
        validate_saved_dashboard(document, "$", report)
        return "saved-dashboard"
    if "title" in document and "query" in document:
        validate_saved_graph(document, "$", report)
        return "saved-graph"

    report.error(
        "unknown document type: expected version 1 (saved graphs), "
        "'doctor.dashboard/v1', or 'doctor.dashboard.bundle/v1'"
    )
    return "unknown"


def render_report(report: Report, kind: str, label: str) -> str:
    """Render a report for the native agent tool and the CLI."""
    lines = [f"{label}: " + ("OK" if not report.errors else "INVALID") + f" ({kind})"]
    for warning in report.warnings:
        lines.append(f"warning: {warning}")
    for error in report.errors:
        lines.append(f"error: {error}")
    return "\n".join(lines)


def cli(argv: list[str]) -> int:
    if len(argv) < 2:
        print(
            "usage: validate_graph_file.py FILE [FILE ...]\n"
            "Validates Saved Graph Bundle v1, Doctor Dashboard v1, and dashboard bundles.",
            file=sys.stderr,
        )
        return 2

    checked = 0
    error_count = 0

    for raw_path in argv[1:]:
        path = Path(raw_path)
        report = Report()
        if not path.is_file():
            print(f"error: {path}: file does not exist", file=sys.stderr)
            error_count += 1
            continue
        try:
            document = load_document(path)
        except Exception as exc:
            print(f"error: {path}: could not parse: {exc}", file=sys.stderr)
            error_count += 1
            continue

        kind = validate_document(document, report)
        checked += 1
        for warning in report.warnings:
            print(f"{path}: warning: {warning}", file=sys.stderr)
        for error in report.errors:
            print(f"error: {path}: {error}", file=sys.stderr)
        error_count += len(report.errors)
        if not report.errors:
            print(f"{path}: OK ({kind})")

    if error_count:
        print(f"{error_count} error(s) in {checked} file(s)", file=sys.stderr)
        return 1

    print(f"validated {checked} file(s)")
    return 0

