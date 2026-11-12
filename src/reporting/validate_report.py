"""Validate every PBIR file of the report against Microsoft's published JSON schemas.

Run from the project root:
    python -m src.reporting.validate_report

Each file declares its schema in "$schema"; schemas (and the schemas they
reference) are downloaded from developer.microsoft.com on first use and cached
in powerbi/.schema_cache/ (git-ignored), so later runs work offline.
"""
from __future__ import annotations

import hashlib
import json
import sys
import urllib.request
from pathlib import Path

from jsonschema import Draft7Validator, Draft202012Validator
from referencing import Registry, Resource

from src.config import PROJECT_ROOT

REPORT_DIR = PROJECT_ROOT / "powerbi" / "marketplace_analytics.Report"
CACHE_DIR = PROJECT_ROOT / "powerbi" / ".schema_cache"
ALLOWED_HOST = "https://developer.microsoft.com/json-schemas/"
# Power BI Desktop can write schema versions before Microsoft publishes them.
# Validate those against the newest published version (checked 2026-11-12).
UNPUBLISHED_FALLBACK = {
    "/visualContainer/2.14.0/": "/visualContainer/2.13.0/",
}


def resolve(uri: str) -> str:
    for missing, published in UNPUBLISHED_FALLBACK.items():
        if missing in uri:
            return uri.replace(missing, published)
    return uri


def fetch(uri: str) -> dict:
    if not uri.startswith(ALLOWED_HOST):
        raise ValueError(f"Refusing to fetch schema from unexpected host: {uri}")
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cached = CACHE_DIR / (hashlib.sha1(uri.encode()).hexdigest() + ".json")
    if cached.exists():
        return json.loads(cached.read_text(encoding="utf-8"))
    with urllib.request.urlopen(uri, timeout=30) as resp:            # noqa: S310 (host checked above)
        data = json.loads(resp.read().decode("utf-8-sig"))
    cached.write_text(json.dumps(data), encoding="utf-8")
    return data


def retrieve(uri: str) -> Resource:
    return Resource.from_contents(fetch(uri))


def validate_file(path: Path, registry: Registry, root: Path) -> list[str]:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    uri = data.get("$schema")
    if not uri:
        return []                                   # e.g. .platform, definition.pbir
    substituted = resolve(uri) != uri
    schema = fetch(resolve(uri))
    cls = Draft202012Validator if "2020-12" in schema.get("$schema", "") else Draft7Validator
    validator = cls(schema, registry=registry)
    errors = []
    for e in validator.iter_errors(data):
        location = "/".join(map(str, e.absolute_path))
        if substituted and location == "$schema":
            continue      # the substitute schema pins its own URL; not a real problem
        errors.append(f"{path.relative_to(root)}: {location}: {e.message[:200]}")
    return errors


def validate(report_dir: Path = REPORT_DIR) -> tuple[int, list[str]]:
    registry = Registry(retrieve=retrieve)
    files = sorted((report_dir / "definition").rglob("*.json"))
    errors: list[str] = []
    for f in files:
        errors.extend(validate_file(f, registry, report_dir))
    return len(files), errors


def main() -> int:
    count, errors = validate()
    print(f"Validated {count} report files against Microsoft's PBIR schemas.")
    if errors:
        print(f"{len(errors)} schema error(s):")
        print("\n".join(errors))
        return 1
    print("All files are valid.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
