"""
CTUIL Compliance PDF Table Extractor
=====================================
Dual-pass extraction strategy:
  Pass 1 — camelot (lattice mode) for bordered tables
  Pass 2 — pdfplumber word-clustering for borderless tables
Falls back gracefully between passes.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import pdfplumber

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _to_snake_case(text: str) -> str:
    """Normalise a column header to snake_case."""
    if not text:
        return "unknown"
    text = text.strip()
    # Replace newlines / extra spaces with single space
    text = re.sub(r"\s+", " ", text)
    # Remove non-alphanumeric chars except spaces
    text = re.sub(r"[^\w\s]", "", text)
    # Replace spaces with underscore and lower-case
    return re.sub(r"\s+", "_", text).lower()


def _clean_cell(value: Any) -> Any:
    """Return None for blank / whitespace-only cells; strip everything else."""
    if value is None:
        return None
    s = str(value).strip()
    if s in ("", "-", "–", "—", "N/A", "NA"):
        return None
    # Collapse internal newlines into a single space
    s = re.sub(r"[\r\n]+", " ", s)
    s = re.sub(r"\s{2,}", " ", s)
    return s


def _detect_report_period(filename: str) -> str:
    """
    Derive the report_period string from the PDF filename.

    Patterns handled (case-insensitive):
      "Sept to Dec'25"  -> "September 2025 - December 2025"
      "June to Aug'25"  -> "June 2025 - August 2025"
      "Jan to March 2026" (or any explicit year)
    """
    name = filename.lower()

    MONTH_MAP = {
        "jan": "January", "feb": "February", "mar": "March", "apr": "April",
        "may": "May", "jun": "June", "jul": "July", "aug": "August",
        "sep": "September", "sept": "September", "spet": "September",
        "oct": "October", "nov": "November", "dec": "December",
        "january": "January", "february": "February", "march": "March",
        "april": "April", "june": "June", "july": "July",
        "august": "August", "september": "September", "october": "October",
        "november": "November", "december": "December",
    }

    # Pattern: "Spet to Dec'25", "Sept to Dec'25", "June to Aug'25" etc.
    pattern = re.compile(
        r"([a-z]+)\s+to\s+([a-z]+)['\s]?(\d{2,4})", re.IGNORECASE
    )
    m = pattern.search(name)
    if m:
        start_raw, end_raw, year_raw = m.group(1), m.group(2), m.group(3)
        year = year_raw if len(year_raw) == 4 else "20" + year_raw
        start = MONTH_MAP.get(start_raw.lower(), start_raw.capitalize())
        end = MONTH_MAP.get(end_raw.lower(), end_raw.capitalize())
        return f"{start} {year} - {end} {year}"

    # Fallback: look for any year in the filename
    year_m = re.search(r"20\d{2}", name)
    if year_m:
        return f"Unknown Period {year_m.group()}"

    return "Unknown Period"


# ---------------------------------------------------------------------------
# pdfplumber extraction
# ---------------------------------------------------------------------------

def _extract_with_pdfplumber(pdf_path: Path) -> list[dict[str, Any]]:
    """
    Extract tables from every page using pdfplumber's built-in table finder.
    Returns a list of raw table dicts: {"page": N, "headers": [...], "rows": [[...]]}
    """
    results: list[dict[str, Any]] = []

    with pdfplumber.open(str(pdf_path)) as pdf:
        for page_num, page in enumerate(pdf.pages, start=1):
            tables = page.extract_tables(
                table_settings={
                    "vertical_strategy": "lines",
                    "horizontal_strategy": "lines",
                    "snap_tolerance": 5,
                    "join_tolerance": 5,
                }
            )
            if not tables:
                # Fallback: try text-based strategy
                tables = page.extract_tables(
                    table_settings={
                        "vertical_strategy": "text",
                        "horizontal_strategy": "lines",
                        "snap_tolerance": 5,
                    }
                )

            for tbl_idx, tbl in enumerate(tables):
                if not tbl or len(tbl) < 2:
                    continue
                results.append(
                    {
                        "page": page_num,
                        "table_index": tbl_idx,
                        "raw_rows": tbl,
                    }
                )

    return results


# ---------------------------------------------------------------------------
# camelot extraction (lattice + stream fallback)
# ---------------------------------------------------------------------------

def _extract_with_camelot(pdf_path: Path) -> list[dict[str, Any]]:
    """
    Attempt camelot lattice extraction first; fall back to stream on failure.
    Returns list of dicts compatible with pdfplumber format.
    """
    try:
        import camelot  # noqa: PLC0415

        results: list[dict[str, Any]] = []

        for flavor in ("lattice", "stream"):
            try:
                tables = camelot.read_pdf(
                    str(pdf_path),
                    pages="all",
                    flavor=flavor,
                    suppress_stdout=True,
                )
                if tables and len(tables) > 0:
                    for tbl in tables:
                        df = tbl.df
                        if df.empty or len(df) < 2:
                            continue
                        raw_rows = df.values.tolist()
                        results.append(
                            {
                                "page": tbl.page,
                                "table_index": 0,
                                "raw_rows": raw_rows,
                                "camelot_accuracy": tbl.accuracy,
                            }
                        )
                    if results:
                        logger.info(
                            "camelot (%s) extracted %d table(s) from %s",
                            flavor,
                            len(results),
                            pdf_path.name,
                        )
                        return results
            except Exception as e:  # noqa: BLE001
                logger.warning("camelot %s failed for %s: %s", flavor, pdf_path.name, e)

        return results

    except ImportError:
        logger.warning("camelot not installed; skipping camelot pass.")
        return []


# ---------------------------------------------------------------------------
# Header detection helpers
# ---------------------------------------------------------------------------

def _looks_like_header_row(row: list) -> bool:
    """Heuristic: a header row has mostly non-numeric short strings."""
    if not row:
        return False
    non_empty = [c for c in row if c and str(c).strip()]
    if not non_empty:
        return False
    numeric_count = sum(
        1
        for c in non_empty
        if re.match(r"^\d[\d,.\-/]*$", str(c).strip())
    )
    return numeric_count / len(non_empty) < 0.4


def _normalise_table(raw_rows: list[list], report_period: str) -> list[dict[str, Any]]:
    """
    Given raw rows (first row = header candidate), return normalised row dicts.
    Injects report_period as the first key.
    """
    if not raw_rows:
        return []

    # Find header row index (first non-empty row that looks like a header)
    header_idx = 0
    for i, row in enumerate(raw_rows):
        if _looks_like_header_row(row):
            header_idx = i
            break

    raw_headers = raw_rows[header_idx]
    headers = [_to_snake_case(str(h)) for h in raw_headers]

    # Deduplicate headers
    seen: dict[str, int] = {}
    deduped: list[str] = []
    for h in headers:
        if h in seen:
            seen[h] += 1
            deduped.append(f"{h}_{seen[h]}")
        else:
            seen[h] = 0
            deduped.append(h)
    headers = deduped

    rows: list[dict[str, Any]] = []
    for row in raw_rows[header_idx + 1 :]:
        if not any(c for c in row if str(c).strip()):
            continue  # skip completely empty rows

        # Pad / truncate to header length
        padded = list(row) + [None] * max(0, len(headers) - len(row))
        padded = padded[: len(headers)]

        record: dict[str, Any] = {"report_period": report_period}
        for col, val in zip(headers, padded):
            record[col] = _clean_cell(val)
        rows.append(record)

    return rows


# ---------------------------------------------------------------------------
# Main public function
# ---------------------------------------------------------------------------

def extract_pdf(pdf_path: Path, prefer_camelot: bool = True) -> dict[str, Any]:
    """
    Full extraction pipeline for a single CTUIL compliance PDF.

    Returns:
        {
            "report_period": str,
            "source_file": str,
            "tables": [{"table_name": str, "rows": [dict, ...]}]
        }
    """
    report_period = _detect_report_period(pdf_path.name)
    logger.info("Processing '%s' | period: %s", pdf_path.name, report_period)

    raw_tables: list[dict[str, Any]] = []

    # --- Pass 1: camelot ---
    if prefer_camelot:
        raw_tables = _extract_with_camelot(pdf_path)

    # --- Pass 2: pdfplumber (always run if camelot returned nothing) ---
    if not raw_tables:
        logger.info("Falling back to pdfplumber for %s", pdf_path.name)
        raw_tables = _extract_with_pdfplumber(pdf_path)

    if not raw_tables:
        logger.warning("No tables extracted from %s", pdf_path.name)

    # Normalise
    output_tables: list[dict[str, Any]] = []
    for idx, raw in enumerate(raw_tables):
        rows = _normalise_table(raw.get("raw_rows", []), report_period)
        if not rows:
            continue
        output_tables.append(
            {
                "table_name": f"financial_closure_deadlines_t{idx + 1}",
                "rows": rows,
            }
        )

    return {
        "report_period": report_period,
        "source_file": pdf_path.name,
        "tables": output_tables,
    }
