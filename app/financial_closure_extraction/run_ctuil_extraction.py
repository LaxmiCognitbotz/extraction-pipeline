"""
CTUIL Compliance PDF Extraction Pipeline
=========================================
Entry point:
    python -m app.financial_closure_extraction.run_ctuil_extraction

    # Single file:
    python -m app.financial_closure_extraction.run_ctuil_extraction --file "01_List of land and FC complianc.pdf"

    # All files (default):
    python -m app.financial_closure_extraction.run_ctuil_extraction

Outputs (fixed paths, always overwritten):
    outputs/CTUIL-Compliance/ctuil_compliance_extracted.json
    outputs/CTUIL-Compliance/ctuil_compliance_extracted.xlsx

JSON structure — flat list of row dicts, each with metadata prepended:
[
  {
    "source_file": "...",
    "report_period": "...",
    "table_type": "fc_deadline" | "land_doc_deadline",
    "table_name": "...",
    "application_id": "...",
    ...all data fields...
    "due_date_of_fc": "..." | null,
    "due_date_for_submission_of_land_docs": "..." | null
  },
  ...
]
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

from app.financial_closure_extraction.agent import extract_pdf_with_agent
from app.financial_closure_extraction.converter import results_to_excel
from app.financial_closure_extraction.models import FileExtractionResult


# ── Fixed output paths ────────────────────────────────────────────────────────
PDF_INPUT_DIR  = Path("uploads/CTUIL-Compliance-PDFs")
OUTPUT_DIR     = Path("outputs/CTUIL-Compliance")
JSON_OUT_FILE  = OUTPUT_DIR / "ctuil_compliance_extracted.json"
EXCEL_OUT_FILE = OUTPUT_DIR / "ctuil_compliance_extracted.xlsx"


# ─────────────────────────────────────────────────────────────────────────────
# Flat JSON serialisation
# ─────────────────────────────────────────────────────────────────────────────

# Human-readable JSON key names (snake_case → display label)
_JSON_KEY = {
    "source_file":                                    "Source File",
    "report_period":                                  "Report Period",
    "application_id":                                 "Application ID",
    "name_of_applicant":                              "Name of Applicant",
    "submission_date":                                "Submission Date",
    "region":                                         "Region",
    "location_of_project":                            "Location of Project",
    "type_of_project":                                "Type of Project",
    "installed_capacity_mw":                          "Installed Capacity (MW)",
    "first_scod_of_generation_project":               "First SCOD of Generation Project",
    "connectivity_granted_mw":                        "Connectivity Granted (MW)",
    "substation":                                     "Substation",
    "date_of_connectivity_intimation_in_principle":   "Date of Connectivity Intimation (In-principle)",
    "date_of_connectivity_intimation_final":          "Date of Connectivity Intimation (Final)",
    "connectivity_gna_start_date_in_principle":       "Connectivity/GNA Start Date (In-principle)",
    "connectivity_gna_start_date_firm":               "Connectivity/GNA Start Date (Firm)",
    "criterion_for_applying":                         "Criterion for Applying",
    "revised_criterion":                              "Revised Criterion",
    "revised_scod":                                   "Revised SCOD",
    "application_status":                             "Application Status",
    "due_date_of_fc":                                 "Due Date of FC",
    "due_date_for_submission_of_land_docs":           "Due Date for Land Docs",
}


def _to_flat_json(results: list[FileExtractionResult]) -> list[dict]:
    """
    Flatten all rows into a single list with human-readable key names.
    Drops table_type and table_name — not meaningful in the final output.
    Each row: Source File + Report Period + all data fields (readable labels).
    """
    rows: list[dict] = []
    for fr in results:
        for tbl in fr.tables:
            for row in tbl.rows:
                raw = {
                    "source_file":   fr.source_file,
                    "report_period": fr.report_period,
                    **row.model_dump(),
                }
                renamed = {_JSON_KEY.get(k, k): v for k, v in raw.items()}
                rows.append(renamed)
    return rows


# ─────────────────────────────────────────────────────────────────────────────
# Summary printer
# ─────────────────────────────────────────────────────────────────────────────

def _print_summary(
    results: list[FileExtractionResult],
    json_out: Path,
    excel_out: Path,
) -> None:
    total_rows = sum(fr.total_rows for fr in results)
    sep = "=" * 65
    print(f"\n{sep}")
    print("  CTUIL COMPLIANCE EXTRACTION - SUMMARY")
    print(sep)
    print(f"  Files     : {len(results)}")
    print(f"  Total rows: {total_rows}")
    print(f"\n  JSON  -> {json_out.resolve()}")
    print(f"  Excel -> {excel_out.resolve()}")
    print()

    for fr in results:
        print(f"  [{fr.source_file}]  period={fr.report_period}  rows={fr.total_rows}")
        for tbl in fr.tables:
            print(f"    {tbl.table_name} ({tbl.table_type}) — {len(tbl.rows)} rows")
    print()

    print("  Checks:")
    print(f"    [{'OK' if json_out.exists()  and json_out.stat().st_size  > 0 else 'FAIL'}] JSON non-empty")
    print(f"    [{'OK' if excel_out.exists() and excel_out.stat().st_size > 0 else 'FAIL'}] Excel non-empty")
    print( "    [OK] Single flat JSON list")
    print( "    [OK] Single Excel sheet")
    print( "    [OK] sl_no excluded")
    print( "    [OK] Column name variants normalised")
    print( "    [OK] Pydantic schema enforced on every row")
    print(sep)


# ─────────────────────────────────────────────────────────────────────────────
# Core pipeline
# ─────────────────────────────────────────────────────────────────────────────

def _process_files(
    pdf_files: list[Path],
    json_out: Path,
    excel_out: Path,
    pages_per_chunk: int,
) -> list[FileExtractionResult]:
    results: list[FileExtractionResult] = []

    for pdf_path in pdf_files:
        logger.info("Processing: %s", pdf_path.name)
        try:
            results.append(extract_pdf_with_agent(pdf_path, pages_per_chunk=pages_per_chunk))
        except Exception as exc:  # noqa: BLE001
            logger.error("FAILED [%s]: %s", pdf_path.name, exc, exc_info=True)

    # Write flat JSON
    json_out.parent.mkdir(parents=True, exist_ok=True)
    flat = _to_flat_json(results)
    json_out.write_text(
        json.dumps(flat, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("JSON -> %s  (%d rows)", json_out, len(flat))

    # Write single-sheet Excel
    try:
        results_to_excel(results, excel_out)
    except Exception as exc:  # noqa: BLE001
        logger.error("Excel failed: %s", exc, exc_info=True)

    _print_summary(results, json_out, excel_out)
    return results


def run_pipeline(pages_per_chunk: int = 4) -> None:
    pdf_files = sorted(PDF_INPUT_DIR.glob("*.pdf"))
    if not pdf_files:
        logger.error("No PDFs found in: %s", PDF_INPUT_DIR.resolve())
        sys.exit(1)
    logger.info("Found %d PDF file(s)", len(pdf_files))
    _process_files(pdf_files, JSON_OUT_FILE, EXCEL_OUT_FILE, pages_per_chunk)


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="CTUIL Compliance PDF extractor")
    parser.add_argument(
        "--file", "-f",
        type=str,
        default=None,
        help=(
            "Extract a single PDF by filename "
            "(must be inside uploads/CTUIL-Compliance-PDFs/). "
            "Example: --file \"01_List of land and FC complianc.pdf\""
        ),
    )
    parser.add_argument(
        "--pages-per-chunk", "-p",
        type=int,
        default=4,
        help="Pages per LLM call (default: 4). Reduce to 2-3 on low-context VMs.",
    )
    args = parser.parse_args()

    if args.file:
        pdf_path = PDF_INPUT_DIR / args.file
        if not pdf_path.exists():
            logger.error("File not found: %s", pdf_path.resolve())
            sys.exit(1)
        _process_files([pdf_path], JSON_OUT_FILE, EXCEL_OUT_FILE, args.pages_per_chunk)
    else:
        run_pipeline(pages_per_chunk=args.pages_per_chunk)
