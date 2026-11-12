"""Tests for Module F: theme file, DAX measure references, and the reporting views.

DAX cannot be executed outside Power BI, so we statically check that every
table[column] and [measure] reference in measures.dax actually exists. That
catches typos before they reach Power BI Desktop.
"""
import json
import re
from pathlib import Path

import pytest

from src.config import PROJECT_ROOT
from src.database import connection
from src.database.powerbi import VIEWS, reconcile

DAX_FILE = PROJECT_ROOT / "powerbi" / "measures" / "measures.dax"
THEME_FILE = PROJECT_ROOT / "powerbi" / "theme" / "marketplace_theme.json"
GUIDE_FILE = PROJECT_ROOT / "powerbi" / "dashboard_guide.md"

DIM_DATE_COLUMNS = {"Date", "Year", "Quarter", "Month Number", "Month", "Year Month",
                    "Month Start", "Weekday", "Weekday Number", "Analysis Window"}


def dax_definitions() -> dict[str, str]:
    """Measure / table name -> expression, parsed from 'Name = expression' blocks."""
    text = "\n".join(line for line in DAX_FILE.read_text(encoding="utf-8").splitlines()
                     if not line.strip().startswith("//"))
    # A definition starts at column 0 with "Name =" ("VAR x =" lines are not definitions).
    name = r"(?!VAR )([A-Za-z_][\w %]*?)"
    parts = re.split(rf"^{name} =\s*$|^{name} = (?=\S)", text, flags=re.M)
    defs, i = {}, 1
    while i < len(parts):
        name = (parts[i] or parts[i + 1]).strip()
        defs[name] = parts[i + 2]
        i += 3
    return defs


def test_theme_is_valid_json_with_eight_colours():
    theme = json.loads(THEME_FILE.read_text(encoding="utf-8"))
    assert len(theme["dataColors"]) == 8
    assert all(re.fullmatch(r"#[0-9A-F]{6}", c) for c in theme["dataColors"])


def test_dax_file_defines_expected_measures():
    defs = dax_definitions()
    for name in ["dim_date", "Total Revenue", "Average Order Value", "Revenue MoM %", "Revenue YoY %",
                 "Total Customers", "New Customers", "Repeat Purchase Rate %", "Late Delivery %",
                 "Avg Delivery Days", "Cancellation %", "Avg Review Score", "Category Revenue Share %"]:
        assert name in defs, name


def test_dax_measure_references_exist():
    defs = dax_definitions()
    measures = set(defs) - {"dim_date"}
    for name, expr in defs.items():
        for ref in re.findall(r"(?<![\w\]])\[([^\]]+)\]", expr):
            if name == "dim_date":
                continue                      # [Date] etc. are columns created inside ADDCOLUMNS
            assert ref in measures, f"{name}: unknown measure [{ref}]"


@pytest.fixture(scope="module")
def view_columns():
    try:
        engine = connection.get_engine()
        connection.check_connection(engine)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"MySQL not reachable: {type(exc).__name__}")
    from sqlalchemy import text
    cols = {}
    with engine.connect() as conn:
        for view, (pbi_name, _key) in VIEWS.items():
            cols[pbi_name] = set(conn.execute(text(f"SELECT * FROM {view} LIMIT 0")).keys())
    cols["dim_date"] = DIM_DATE_COLUMNS
    return cols, engine


@pytest.mark.integration
def test_dax_column_references_exist_in_views(view_columns):
    cols, _ = view_columns
    for name, expr in dax_definitions().items():
        for table, column in re.findall(r"\b(\w+)\[([^\]]+)\]", expr):
            assert table in cols, f"{name}: unknown table {table}"
            assert column in cols[table], f"{name}: {table}[{column}] does not exist"


@pytest.mark.integration
def test_guide_references_existing_columns(view_columns):
    cols, _ = view_columns
    guide = GUIDE_FILE.read_text(encoding="utf-8")
    for table, column in re.findall(r"`(\w+)\[([^\]]+)\]`", guide):
        if table in cols:
            assert column in cols[table], f"guide: {table}[{column}] does not exist"


@pytest.mark.integration
def test_views_reconcile_with_base_tables(view_columns):
    _, engine = view_columns
    result = reconcile(engine)
    assert result["match"].all(), result.to_string()
