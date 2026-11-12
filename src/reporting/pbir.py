"""Small helpers that produce Power BI report (PBIR) JSON fragments.

The shapes mirror what Power BI Desktop itself writes (see the visuals saved in
powerbi/marketplace_analytics.Report), so generated visuals look exactly like
hand-made ones to Power BI.
"""
from __future__ import annotations

import hashlib

SCHEMA_BASE = "https://developer.microsoft.com/json-schemas/fabric/item/report/definition"
VISUAL_SCHEMA = f"{SCHEMA_BASE}/visualContainer/2.14.0/schema.json"
PAGE_SCHEMA = f"{SCHEMA_BASE}/page/2.1.0/schema.json"
PAGES_SCHEMA = f"{SCHEMA_BASE}/pagesMetadata/1.1.0/schema.json"
MEASURE_TABLE = "_Measure"


def stable_id(*parts: str) -> str:
    """20-character hex id (the format Power BI uses), stable across re-runs."""
    return hashlib.md5("|".join(parts).encode("utf-8")).hexdigest()[:20]


# --------------------------------------------------------------------------- #
# Literals
# --------------------------------------------------------------------------- #
def lit(value) -> dict:
    """Wrap a Python value as a PBIR literal expression."""
    if isinstance(value, bool):
        text = "true" if value else "false"
    elif isinstance(value, (int, float)):
        text = f"{value}D"
    else:
        text = "'" + str(value).replace("'", "''") + "'"
    return {"expr": {"Literal": {"Value": text}}}


def color(hex_code: str) -> dict:
    return {"solid": {"color": lit(hex_code)}}


def props(**kwargs) -> list[dict]:
    """objects.<name> = [{"properties": {...}}] with literal values."""
    return [{"properties": {k: (v if isinstance(v, dict) else lit(v)) for k, v in kwargs.items()}}]


# --------------------------------------------------------------------------- #
# Fields and projections
# --------------------------------------------------------------------------- #
def column(entity: str, prop: str) -> dict:
    return {"Column": {"Expression": {"SourceRef": {"Entity": entity}}, "Property": prop}}


def measure(prop: str) -> dict:
    return {"Measure": {"Expression": {"SourceRef": {"Entity": MEASURE_TABLE}}, "Property": prop}}


def field_name(field: dict) -> tuple[str, str]:
    inner = field.get("Column") or field.get("Measure")
    return inner["Expression"]["SourceRef"]["Entity"], inner["Property"]


def projection(field: dict, active: bool = False, display_name: str | None = None) -> dict:
    entity, prop = field_name(field)
    p = {"field": field, "queryRef": f"{entity}.{prop}", "nativeQueryRef": prop}
    if active:
        p["active"] = True
    if display_name:
        p["displayName"] = display_name
    return p


def sort_by(field: dict, direction: str = "Descending") -> dict:
    return {"sort": [{"field": field, "direction": direction}], "isDefaultSort": True}


# --------------------------------------------------------------------------- #
# Filters
# --------------------------------------------------------------------------- #
def in_filter(entity: str, prop: str, values: list, name: str) -> dict:
    """Categorical filter: entity[prop] IN values (strings or ints)."""
    def literal(v):
        return {"Literal": {"Value": f"{v}L" if isinstance(v, int) else "'" + str(v) + "'"}}
    return {
        "name": name,
        "field": column(entity, prop),
        "type": "Categorical",
        "filter": {
            "Version": 2,
            "From": [{"Name": "f", "Entity": entity, "Type": 0}],
            "Where": [{"Condition": {"In": {
                "Expressions": [{"Column": {"Expression": {"SourceRef": {"Source": "f"}}, "Property": prop}}],
                "Values": [[literal(v)] for v in values],
            }}}],
        },
        "howCreated": "User",
    }


def top_n_filter(entity: str, prop: str, by_measure: str, n: int, name: str) -> dict:
    """Top N filter: keep the N values of entity[prop] with the highest measure."""
    return {
        "name": name,
        "field": column(entity, prop),
        "type": "TopN",
        "filter": {
            "Version": 2,
            "From": [
                {"Name": "subquery", "Expression": {"Subquery": {"Query": {
                    "Version": 2,
                    "From": [{"Name": "d", "Entity": entity, "Type": 0},
                             {"Name": "m", "Entity": MEASURE_TABLE, "Type": 0}],
                    "Select": [{"Column": {"Expression": {"SourceRef": {"Source": "d"}}, "Property": prop},
                                "Name": "field"}],
                    "OrderBy": [{"Direction": 2, "Expression": {"Measure": {
                        "Expression": {"SourceRef": {"Source": "m"}}, "Property": by_measure}}}],
                    "Top": n,
                }}}, "Type": 2},
                {"Name": "d", "Entity": entity, "Type": 0},
            ],
            "Where": [{"Condition": {"In": {
                "Expressions": [{"Column": {"Expression": {"SourceRef": {"Source": "d"}}, "Property": prop}}],
                "Table": {"SourceRef": {"Source": "subquery"}},
            }}}],
        },
        "howCreated": "User",
    }
