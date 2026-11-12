"""Read raw CSV files into pandas DataFrames, exactly as provided.

The raw files are never modified. Dates are parsed and zip codes are kept as
text here because those are format decisions, not cleaning decisions.
"""
from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from src.config import RAW_DIR
from src.ingestion.registry import TABLES, TableSpec

logger = logging.getLogger(__name__)


class MissingDatasetError(FileNotFoundError):
    """Raised when an expected raw CSV is not in data/raw/."""


def load_raw_table(name: str, raw_dir: Path = RAW_DIR, parse_dates: bool = True) -> pd.DataFrame:
    """Load one raw table by its logical name (e.g. 'orders')."""
    if name not in TABLES:
        raise KeyError(f"Unknown table '{name}'. Known tables: {sorted(TABLES)}")
    spec: TableSpec = TABLES[name]
    path = raw_dir / spec.file_name
    if not path.exists():
        raise MissingDatasetError(
            f"{path} not found. Download the Olist dataset from "
            "https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce "
            f"and extract the CSVs into {raw_dir}."
        )

    dtype = {col: "string" for col in spec.string_columns}
    df = pd.read_csv(path, dtype=dtype, encoding=spec.encoding)

    if parse_dates:
        for col in spec.date_columns:
            # errors="coerce": unparseable values become NaT so they can be
            # counted as invalid in the quality report instead of crashing.
            df[col] = pd.to_datetime(df[col], errors="coerce")

    logger.info("Loaded %-22s %8d rows x %2d cols", name, len(df), df.shape[1])
    return df


def load_all_raw(raw_dir: Path = RAW_DIR, parse_dates: bool = True) -> dict[str, pd.DataFrame]:
    """Load every table listed in the registry."""
    return {name: load_raw_table(name, raw_dir, parse_dates) for name in TABLES}
