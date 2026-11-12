"""Text standardization helpers for city names and other categorical text."""
from __future__ import annotations

import re

import pandas as pd

from src.ingestion.profiler import VALID_BR_STATES, normalize_text

_STATE_CODES_LOWER = {s.lower() for s in VALID_BR_STATES}
_APOSTROPHES = re.compile(r"[´`'’]")
# Anything after a separator is a state / country suffix: "auriflama/sp",
# "novo hamburgo, rio grande do sul, brasil", "rio de janeiro \\rio de janeiro"
_SUFFIX_SEPARATORS = re.compile(r"[/,\\]")


def clean_city(value) -> str | None:
    """Standardize a city name, or return None if it is not a usable city.

    Steps:
      1. lower-case, strip accents, collapse whitespace    'São  Paulo' -> 'sao paulo'
      2. apostrophes -> space                              "d´oeste"   -> 'd oeste'
      3. reject junk: emails or values containing digits   '04482255'  -> None
      4. drop state/country suffixes after / , or \\        'pinhais/pr' -> 'pinhais'
      5. reject values that are only a state code          'sp / sp'   -> None
    """
    if value is None or (isinstance(value, float) and pd.isna(value)) or value is pd.NA:
        return None
    text = normalize_text(_APOSTROPHES.sub(" ", str(value)))
    if not text or "@" in text or any(ch.isdigit() for ch in text):
        return None
    text = _SUFFIX_SEPARATORS.split(text, maxsplit=1)[0].strip()
    if not text or text in _STATE_CODES_LOWER:
        return None
    return text


def clean_city_series(series: pd.Series) -> pd.Series:
    """Vectorized wrapper: clean each distinct value once, then map back."""
    mapping = {v: clean_city(v) for v in series.dropna().unique()}
    return series.map(mapping).astype("string")


def clean_state_series(series: pd.Series) -> pd.Series:
    """Upper-case and trim state codes; anything not a valid UF becomes NA."""
    cleaned = series.astype("string").str.strip().str.upper()
    return cleaned.where(cleaned.isin(VALID_BR_STATES))


def clean_zip_series(series: pd.Series) -> pd.Series:
    """Zip prefixes as 5-character text with leading zeros preserved."""
    return series.astype("string").str.strip().str.zfill(5)
