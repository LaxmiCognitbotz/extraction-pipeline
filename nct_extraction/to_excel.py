"""Convert NCT extraction results (JSON) → Excel workbook.

Creates a single sheet with one row per element, columns matching
the TBCB UC report structure.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def write_excel(results: list[dict], output_path: str) -> None:
    """Write a list of NCTExtractionResult dicts to an Excel file.

    Args:
        results: List of dicts, each with 'meeting_name' and 'elements'.
        output_path: Path to the .xlsx output file.
    """
    try:
        import openpyxl
    except ImportError:
        # Fallback to CSV if openpyxl not installed
        _write_csv_fallback(results, output_path)
        return

    from openpyxl import Workbook
    from openpyxl.styles import Font, Alignment, PatternFill, Border, Side

    wb = Workbook()
    ws = wb.active
    ws.title = "NCT Extraction"

    # ── Header styling ──
    header_font = Font(name="Calibri", bold=True, size=11, color="FFFFFF")
    header_fill = PatternFill(start_color="2F5496", end_color="2F5496", fill_type="solid")
    header_align = Alignment(horizontal="center", vertical="center", wrap_text=True)
    thin_border = Border(
        left=Side(style="thin"),
        right=Side(style="thin"),
        top=Side(style="thin"),
        bottom=Side(style="thin"),
    )

    headers = [
        "S.No.",
        "Meeting Name",
        "Element Code",
        "Transmission Scheme",
        "Transmission Scope",
        "MVA",
        "Length",
        "Execution Timeline",
        "Tender Issuing Authority",
        "Project Cost (Cr.) (NCT)",
        "Source",
    ]

    for col, header in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=header)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = header_align
        cell.border = thin_border

    # ── Data rows ──
    data_font = Font(name="Calibri", size=10)
    data_align = Alignment(vertical="top", wrap_text=True)
    row_num = 2
    serial = 1

    for result in results:
        meeting = result.get("meeting_name", "")
        elements = result.get("elements", [])

        for elem in elements:
            length_val = None
            if "Physical Progress S/s of Tx. Line" in elem and isinstance(elem["Physical Progress S/s of Tx. Line"], dict):
                length_val = elem["Physical Progress S/s of Tx. Line"].get("Length")
            else:
                length_val = elem.get("length_km")

            values = [
                serial,
                meeting,
                elem.get("Element Code") or elem.get("element_code", ""),
                elem.get("Transmission Scheme") or elem.get("scheme_name", ""),
                elem.get("Transmission Scope") or elem.get("scope", ""),
                elem.get("MVA") if "MVA" in elem else elem.get("capacity_mva"),
                length_val,
                elem.get("Execution Timeline") or elem.get("execution_timeline", ""),
                elem.get("Tender Issuing Authority") or elem.get("tender_issuing_authority", ""),
                elem.get("Project Cost (Cr.) (NCT)") if "Project Cost (Cr.) (NCT)" in elem else elem.get("project_cost_cr"),
                elem.get("Source") or elem.get("source", ""),
            ]

            for col, value in enumerate(values, 1):
                cell = ws.cell(row=row_num, column=col, value=value)
                cell.font = data_font
                cell.alignment = data_align
                cell.border = thin_border

            row_num += 1
            serial += 1

    # ── Column widths ──
    col_widths = [8, 20, 12, 45, 60, 14, 12, 25, 22, 18, 20]
    for i, width in enumerate(col_widths, 1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = width

    # ── Freeze header row ──
    ws.freeze_panes = "A2"

    # ── Auto-filter ──
    ws.auto_filter.ref = f"A1:{openpyxl.utils.get_column_letter(len(headers))}{row_num - 1}"

    wb.save(output_path)
    print(f"[excel] Wrote {serial - 1} rows to {output_path}")


def _write_csv_fallback(results: list[dict], output_path: str) -> None:
    """Fallback: write CSV if openpyxl is not available."""
    import csv

    csv_path = output_path.replace(".xlsx", ".csv")
    headers = [
        "S.No.", "Meeting Name", "Element Code", "Transmission Scheme", "Transmission Scope",
        "MVA", "Length", "Execution Timeline", "Tender Issuing Authority",
        "Project Cost (Cr.) (NCT)", "Source",
    ]

    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(headers)

        serial = 1
        for result in results:
            meeting = result.get("meeting_name", "")
            for elem in result.get("elements", []):
                length_val = None
                if "Physical Progress S/s of Tx. Line" in elem and isinstance(elem["Physical Progress S/s of Tx. Line"], dict):
                    length_val = elem["Physical Progress S/s of Tx. Line"].get("Length")
                else:
                    length_val = elem.get("length_km", "")

                writer.writerow([
                    serial,
                    meeting,
                    elem.get("Element Code") or elem.get("element_code", ""),
                    elem.get("Transmission Scheme") or elem.get("scheme_name", ""),
                    elem.get("Transmission Scope") or elem.get("scope", ""),
                    elem.get("MVA", "") if "MVA" in elem else elem.get("capacity_mva", ""),
                    length_val,
                    elem.get("Execution Timeline") or elem.get("execution_timeline", ""),
                    elem.get("Tender Issuing Authority") or elem.get("tender_issuing_authority", ""),
                    elem.get("Project Cost (Cr.) (NCT)", "") if "Project Cost (Cr.) (NCT)" in elem else elem.get("project_cost_cr", ""),
                    elem.get("Source") or elem.get("source", ""),
                ])
                serial += 1

    print(f"[csv] Wrote {serial - 1} rows to {csv_path} (openpyxl not available)")
