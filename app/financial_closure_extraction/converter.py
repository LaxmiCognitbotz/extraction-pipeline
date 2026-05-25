"""
Excel converter for CTUIL Compliance extraction results.
=========================================================

Accepts a list of FileExtractionResult objects and writes a styled .xlsx
workbook.  One worksheet is created per logical sub-table.

Column ordering contract
─────────────────────────
  Col A  : report_period          ← always first
  Col B  : sl_no
  Col C  : application_id
  Col D  : name_of_applicant
  ... remaining fields in Pydantic model declaration order ...
  Last   : due_date_of_fc  OR  due_date_for_submission_of_land_docs  ← always last

All date financial_closure_extraction strings (no Excel date formatting)
to prevent format corruption.  Cells are never merged.
"""

from __future__ import annotations

import logging
from pathlib import Path

import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from app.financial_closure_extraction.models import (
    FCDeadlineTable,
    FileExtractionResult,
    LandDocDeadlineTable,
)

logger = logging.getLogger(__name__)

# ── Style constants ────────────────────────────────────────────────────────────
_HEADER_FILL = PatternFill("solid", fgColor="1F3864")   # dark navy
_HEADER_FONT = Font(bold=True, color="FFFFFF", name="Calibri", size=10)
_DATA_FONT   = Font(name="Calibri", size=10)
_ALT_FILL    = PatternFill("solid", fgColor="EAF0FB")   # pale blue alternating row

# Column ordering: fields always first, fields always last
_PRIORITY_FIRST = ["report_period", "sl_no", "application_id", "name_of_applicant"]
_PRIORITY_LAST  = ["due_date_of_fc", "due_date_for_submission_of_land_docs"]


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _ordered_headers(sample_row_dict: dict) -> list[str]:
    """
    Return column headers in the canonical order:
      PRIORITY_FIRST → all other fields (model declaration order) → PRIORITY_LAST
    """
    all_keys = list(sample_row_dict.keys())
    ordered: list[str] = []

    for k in _PRIORITY_FIRST:
        if k in all_keys:
            ordered.append(k)

    for k in all_keys:
        if k not in ordered and k not in _PRIORITY_LAST:
            ordered.append(k)

    for k in _PRIORITY_LAST:
        if k in all_keys:
            ordered.append(k)

    return ordered


def _auto_size_columns(ws: openpyxl.worksheet.worksheet.Worksheet) -> None:
    """Set each column width to fit its longest cell value (max 60 chars)."""
    for col_cells in ws.columns:
        max_len = 0
        col_letter = get_column_letter(col_cells[0].column)
        for cell in col_cells:
            try:
                cell_len = len(str(cell.value)) if cell.value is not None else 0
                max_len = max(max_len, cell_len)
            except Exception:  # noqa: BLE001
                pass
        ws.column_dimensions[col_letter].width = min(max_len + 4, 60)


def _unique_sheet_title(wb: openpyxl.Workbook, candidate: str) -> str:
    """Return a unique sheet title within the workbook (Excel limit: 31 chars)."""
    base = candidate[:28]
    title = base
    counter = 1
    existing = {s.title for s in wb.worksheets}
    while title in existing:
        title = f"{base}_{counter}"
        counter += 1
    return title


def _write_table_sheet(
    wb: openpyxl.Workbook,
    sheet_title: str,
    headers: list[str],
    rows: list[dict],
) -> None:
    """Write one table to one new worksheet with styled header + data rows."""
    ws = wb.create_sheet(title=_unique_sheet_title(wb, sheet_title))

    # Header row
    for col_idx, header in enumerate(headers, start=1):
        cell = ws.cell(row=1, column=col_idx, value=header)
        cell.font      = _HEADER_FONT
        cell.fill      = _HEADER_FILL
        cell.alignment = Alignment(
            horizontal="center", vertical="center", wrap_text=True
        )
    ws.row_dimensions[1].height = 32

    # Data rows
    for row_idx, record in enumerate(rows, start=2):
        apply_alt = (row_idx % 2 == 0)
        for col_idx, key in enumerate(headers, start=1):
            val  = record.get(key)
            cell = ws.cell(row=row_idx, column=col_idx, value=val)
            cell.font      = _DATA_FONT
            cell.alignment = Alignment(vertical="top", wrap_text=True)
            if apply_alt:
                cell.fill = _ALT_FILL

    _auto_size_columns(ws)
    ws.freeze_panes = "A2"   # keep header visible while scrolling


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def results_to_excel(
    results: list[FileExtractionResult],
    output_path: Path,
) -> Path:
    """
    Convert a list of FileExtractionResult objects into a styled Excel workbook.

    One sheet is created per table, named:
      <source_file_stem (truncated)>_<table_name>

    Args:
        results:     List of per-file extraction results.
        output_path: Target .xlsx file path (parent directories created automatically).

    Returns:
        The resolved output_path.
    """
    wb = openpyxl.Workbook()
    wb.remove(wb.active)   # remove the default blank sheet
    total_sheets = 0

    for file_result in results:
        # Use first 18 chars of stem to keep sheet titles short
        source_stem = Path(file_result.source_file).stem[:18]

        for tbl in file_result.tables:
            if not tbl.rows:
                logger.warning(
                    "  [%s / %s]  0 rows — sheet skipped",
                    file_result.source_file, tbl.table_name,
                )
                continue

            # Build ordered column list from the first row's keys
            first_row_dict = tbl.rows[0].model_dump()
            headers = _ordered_headers(first_row_dict)

            # Serialise all rows
            serialised = [r.model_dump() for r in tbl.rows]

            sheet_title = f"{source_stem}_{tbl.table_name}"
            _write_table_sheet(wb, sheet_title, headers, serialised)
            total_sheets += 1

    if total_sheets == 0:
        ws = wb.create_sheet("No Data")
        ws["A1"] = "No tables were extracted from the provided PDFs."
        logger.warning("No data sheets written — workbook contains placeholder only.")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(str(output_path))
    logger.info(
        "Excel workbook saved: %s  (%d sheet(s))", output_path, total_sheets
    )
    return output_path
