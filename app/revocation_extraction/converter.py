"""
Excel converter for CTUIL Revocation (24.6) extraction.

Schema-driven: all 14 core columns are fixed with exact PDF-matching labels.
Styled with Navy Blue header and alternating Ice Blue row fills.
"""

from __future__ import annotations

import logging
from pathlib import Path

import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from app.revocation_extraction.models import RevocationRecord

logger = logging.getLogger(__name__)

# ── Styles ─────────────────────────────────────────────────────────────────────
_HEADER_FILL = PatternFill("solid", fgColor="1F3864")
_HEADER_FONT = Font(bold=True, color="FFFFFF", name="Calibri", size=10)
_META_FILL   = PatternFill("solid", fgColor="2E5FA3")
_META_FONT   = Font(bold=True, color="FFFFFF", name="Calibri", size=10)
_DATE_FILL   = PatternFill("solid", fgColor="FFF2CC")
_DATE_FONT   = Font(bold=True, color="7B3F00", name="Calibri", size=10)
_LTA_FILL    = PatternFill("solid", fgColor="E2EFDA")
_LTA_FONT    = Font(bold=True, color="375623", name="Calibri", size=10)
_DATA_FONT   = Font(name="Calibri", size=10)
_ALT_FILL    = PatternFill("solid", fgColor="EAF0FB")
_THIN        = Side(style="thin", color="B0B0B0")
_THIN_BORDER = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)

# ── Column definitions: (model_field, excel_label, width) ──────────────────────
_COLUMNS = [
    ("source_file",                       "Source File",                                   28),
    ("upto_month",                        "Upto Month",                                    12),
    ("application_id",                    "Application ID",                                18),
    ("lta_id",                            "LTA ID",                                        35),
    ("applicant_name",                    "Applicant Name",                                38),
    ("region",                            "Region",                                        10),
    ("criterion",                         "Criterion",                                     14),
    ("type_of_project",                   "Type of Project",                               14),
    ("present_connectivity_deemed_gna",   "Present Connectivity / deemed GNA (MW)",        20),
    ("substation",                        "Substation",                                    22),
    ("connectivity_gna_start_date_firm",  "Connectivity / GNA Start Date (Firm)",          22),
    ("connectivity_status",               "Connectivity Status",                           22),
    ("date_connectivity_gna_made_effective", "Date Connectivity / GNA Made Effective",     22),
    ("generation_commissioning_status",   "Generation Commissioning Status",               26),
    ("scod_as_per_application",           "SCOD as per Application",                       18),
    ("updated_revised_scod",              "Updated / Revised SCOD",                        18),
    ("compliance_due_date",               "24.6 Compliance Due Date",                      22),
]

_META_FIELDS = {"source_file", "upto_month"}
_LTA_FIELDS  = {"lta_id"}
_DATE_FIELDS = {"compliance_due_date"}


def records_to_excel(
    records: list[RevocationRecord],
    output_path: Path,
) -> Path:
    """Write all revocation records to a single styled Excel sheet."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Revocations 24.6"

    # ── Header row ────────────────────────────────────────────────────────────
    for col_idx, (field, label, _) in enumerate(_COLUMNS, start=1):
        cell = ws.cell(row=1, column=col_idx, value=label)
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = _THIN_BORDER

        if field in _LTA_FIELDS:
            cell.fill = _LTA_FILL
            cell.font = _LTA_FONT
        elif field in _META_FIELDS:
            cell.fill = _META_FILL
            cell.font = _META_FONT
        elif field in _DATE_FIELDS:
            cell.fill = _DATE_FILL
            cell.font = _DATE_FONT
        else:
            cell.fill = _HEADER_FILL
            cell.font = _HEADER_FONT

    ws.row_dimensions[1].height = 42

    # ── Data rows ─────────────────────────────────────────────────────────────
    for row_idx, rec in enumerate(records, start=2):
        d = rec.model_dump()
        apply_alt = (row_idx % 2 == 0)

        for col_idx, (field, _, _w) in enumerate(_COLUMNS, start=1):
            val = d.get(field)
            cell = ws.cell(row=row_idx, column=col_idx, value=val)
            cell.font = _DATA_FONT
            cell.alignment = Alignment(vertical="top", wrap_text=True)
            cell.border = _THIN_BORDER

            if field in _DATE_FIELDS and val:
                cell.fill = PatternFill("solid", fgColor="FFFACD")
            elif field in _LTA_FIELDS and val:
                cell.fill = PatternFill("solid", fgColor="F0FFF0")
            elif apply_alt:
                cell.fill = _ALT_FILL

    # ── Column widths ─────────────────────────────────────────────────────────
    for col_idx, (_, _, width) in enumerate(_COLUMNS, start=1):
        ws.column_dimensions[get_column_letter(col_idx)].width = width

    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions

    output_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(str(output_path))
    logger.info(
        "Excel saved: %s  (%d rows, %d columns)",
        output_path, len(records), len(_COLUMNS)
    )
    return output_path
