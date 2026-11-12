"""Tests for the generated Power BI report (PBIR) and its semantic model (TMDL).

Power BI cannot be run in a test, so these checks catch what would make the
report fail to open or show broken visuals:
  * every column / measure a visual uses exists in the model,
  * the five pages and their synced slicers are present,
  * the builder is idempotent (a second run changes nothing),
  * measure names and currency formats are correct.
"""
import json
import re
from pathlib import Path

import pytest

from src.config import PROJECT_ROOT
from src.reporting.build_dashboard import build

PBIP = PROJECT_ROOT / "powerbi"
MODEL = PBIP / "marketplace_analytics.SemanticModel" / "definition" / "tables"
REPORT = PBIP / "marketplace_analytics.Report" / "definition"

pytestmark = pytest.mark.skipif(not REPORT.exists(), reason="PBIP report not present")

EXPECTED_PAGES = ["Executive Overview", "Product and Category Analysis", "Customer Analytics",
                  "Regional and Delivery Analytics", "Payment and Review Analysis"]
SLICER_GROUPS = {"slicer_date", "slicer_category", "slicer_state", "slicer_status", "slicer_payment"}


def model_objects() -> tuple[dict[str, set[str]], dict[str, str]]:
    """table -> column names, and measure name -> home table, parsed from TMDL."""
    columns: dict[str, set[str]] = {}
    measures: dict[str, str] = {}
    for f in MODEL.glob("*.tmdl"):
        text = f.read_text(encoding="utf-8")
        table = re.search(r"^table (.+)$", text, re.M).group(1).strip().strip("'")
        columns[table] = {m.strip().strip("'") for m in
                          re.findall(r"^\tcolumn (.+?)(?: =.*)?$", text, re.M)}
        for m in re.findall(r"^\tmeasure (.+?) =", text, re.M):
            measures[m.strip().strip("'")] = table
    return columns, measures


TMDL_OBJECTS = ("table ", "column ", "measure ", "partition ", "hierarchy ", "level ", "annotation ",
                "relationship ", "expression ", "model ", "ref ", "cultureInfo ", "database",
                "calculationGroup", "calculationItem ", "linguisticMetadata", "variation ", "role ",
                "perspective", "changedProperty", "extendedProperty ", "dataAccessOptions", "formatStringDefinition")


def tmdl_structure_errors(text: str) -> list[str]:
    """Find lines indented under something that cannot have children.

    In TMDL a line indented one level deeper than its parent must belong to an
    object (column, measure, partition, ...) or continue a multi-line value
    ("source =", a measure expression, a ``` block). An annotation left under a
    plain property such as `lineageTag:` is exactly the error Power BI reports
    as "Invalid indentation".
    """
    errors, stack, fence = [], [], False      # stack of (indent, line)
    for no, line in enumerate(text.splitlines(), 1):
        if line.strip().startswith("```"):
            fence = not fence
            continue
        if fence or not line.strip():
            continue
        indent = len(line) - len(line.lstrip("\t"))
        while stack and stack[-1][0] >= indent:
            stack.pop()
        if stack:
            parent = stack[-1][1].strip()
            parent_ok = parent.startswith(TMDL_OBJECTS) or parent.endswith("=") or " = " in parent \
                or parent.startswith(("source", "expression"))
            # deeper lines inside a multi-line expression are always allowed
            if not parent_ok and indent == stack[-1][0] + 1:
                errors.append(f"line {no}: '{line.strip()[:60]}' is indented under '{parent[:40]}'")
        stack.append((indent, line))
    return errors


def test_tmdl_files_have_valid_structure():
    problems = []
    for f in sorted(MODEL.parent.rglob("*.tmdl")):
        problems += [f"{f.name}: {e}" for e in tmdl_structure_errors(f.read_text(encoding="utf-8"))]
    assert not problems, "\n".join(problems)


def test_tmdl_check_catches_orphaned_annotation():
    bad = "table t\n\tlineageTag: x\n\n\t\tannotation PBI_FormatHint = {}\n\n\tcolumn c\n\t\tdataType: string\n"
    good = "table t\n\tlineageTag: x\n\n\tcolumn c\n\t\tdataType: string\n\n\t\tannotation A = 1\n"
    assert tmdl_structure_errors(bad) and not tmdl_structure_errors(good)


def field_refs(node, out=None):
    """Collect every Column/Measure reference that names an Entity directly."""
    out = [] if out is None else out
    if isinstance(node, dict):
        for kind in ("Column", "Measure"):
            ref = node.get(kind)
            if isinstance(ref, dict) and "Entity" in ref.get("Expression", {}).get("SourceRef", {}):
                out.append((kind, ref["Expression"]["SourceRef"]["Entity"], ref["Property"]))
        for v in node.values():
            field_refs(v, out)
    elif isinstance(node, list):
        for v in node:
            field_refs(v, out)
    return out


def visuals():
    for f in sorted(REPORT.glob("pages/*/visuals/*/visual.json")):
        yield f, json.loads(f.read_text(encoding="utf-8"))


def test_every_referenced_field_exists_in_model():
    columns, measures = model_objects()
    problems = []
    for f, v in visuals():
        for kind, entity, prop in field_refs(v):
            if kind == "Column" and prop not in columns.get(entity, set()):
                problems.append(f"{f.parent.name}: column {entity}[{prop}] not in model")
            if kind == "Measure" and measures.get(prop) != entity:
                problems.append(f"{f.parent.name}: measure [{prop}] not in table {entity}")
    assert not problems, "\n".join(problems)


def test_five_pages_in_order():
    meta = json.loads((REPORT / "pages" / "pages.json").read_text(encoding="utf-8"))
    names = [json.loads((REPORT / "pages" / p / "page.json").read_text(encoding="utf-8"))["displayName"]
             for p in meta["pageOrder"]]
    assert names[:5] == EXPECTED_PAGES


def test_every_page_has_the_five_synced_slicers():
    meta = json.loads((REPORT / "pages" / "pages.json").read_text(encoding="utf-8"))
    for page in meta["pageOrder"][:5]:
        groups = set()
        for f in (REPORT / "pages" / page / "visuals").glob("*/visual.json"):
            v = json.loads(f.read_text(encoding="utf-8"))["visual"]
            if v["visualType"] == "slicer":
                groups.add(v["syncGroup"]["groupName"])
        assert groups == SLICER_GROUPS, page


def test_visuals_fit_on_the_page():
    for f, v in visuals():
        p = v["position"]
        assert p["x"] >= 0 and p["y"] >= 0, f
        assert p["x"] + p["width"] <= 1920 + 0.5 and p["y"] + p["height"] <= 1080 + 0.5, f


def test_builder_is_idempotent():
    assert build(dry_run=True) == []


def test_revenue_measure_fixed_and_currency_is_brl():
    columns, measures = model_objects()
    assert measures.get("Total Revenue") == "_Measure"
    assert "Total Reveue" not in measures
    text = (MODEL / "_Measure.tmdl").read_text(encoding="utf-8")
    assert r"\$#" not in text, "a measure still uses a $ format"
    block = text.split("measure 'Total Revenue' =")[1].split("lineageTag")[0]
    assert r"\R\$" in block


def test_no_unfiltered_page_level_window_filter_on_kpis():
    """KPI cards must show all-data totals (expected values), so no page-level window filter."""
    for page in (REPORT / "pages").iterdir():
        if page.is_dir():
            pj = json.loads((page / "page.json").read_text(encoding="utf-8"))
            assert "filterConfig" not in pj, pj["displayName"]


@pytest.mark.integration
def test_report_matches_microsoft_schemas():
    from src.reporting.validate_report import validate
    try:
        count, errors = validate()
    except OSError as exc:            # offline and no cached schemas
        pytest.skip(f"schemas unavailable: {exc}")
    assert count > 0 and not errors, "\n".join(errors)
