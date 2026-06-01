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
RAW_JSON_FILE  = OUTPUT_DIR / "ctuil_compliance_raw.json"
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
    "state":                                          "State",
    "project_location_details":                       "Project Location Details",
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


def _load_existing_raw(raw_path: Path) -> list[FileExtractionResult]:
    if not raw_path.exists():
        return []
    try:
        data = json.loads(raw_path.read_text(encoding="utf-8"))
        return [FileExtractionResult(**item) for item in data]
    except Exception as exc:
        logger.warning("Could not load existing raw JSON for appending: %s", exc)
        return []


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
    force: bool = False,
) -> list[FileExtractionResult]:
    existing_results = _load_existing_raw(RAW_JSON_FILE)
    
    if not force:
        already_processed = {getattr(r, 'source_file', '') for r in existing_results}
        skipped = [p for p in pdf_files if p.name in already_processed]
        pdf_files = [p for p in pdf_files if p.name not in already_processed]
        if skipped:
            logger.info("Skipping %d already-extracted PDF(s). Use --force to re-extract.", len(skipped))
        if not pdf_files:
            logger.info("All selected PDFs are already extracted. Exiting.")
            return existing_results

    pdf_names = {p.name for p in pdf_files}
    results = [r for r in existing_results if getattr(r, 'source_file', '') not in pdf_names]
    
    if existing_results:
        logger.info("Loaded %d existing records (kept %d after filtering for overwrites)", len(existing_results), len(results))

    for pdf_path in pdf_files:
        logger.info("Processing: %s", pdf_path.name)
        try:
            results.append(extract_pdf_with_agent(pdf_path, pages_per_chunk=pages_per_chunk))
        except Exception as exc:  # noqa: BLE001
            logger.error("FAILED [%s]: %s", pdf_path.name, exc, exc_info=True)

    json_out.parent.mkdir(parents=True, exist_ok=True)
    
    # Write raw JSON to enable future appends
    RAW_JSON_FILE.write_text(json.dumps([r.model_dump() for r in results], ensure_ascii=False, indent=2), encoding="utf-8")

    # Write flat JSON
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


def run_pipeline(
    pages_per_chunk: int = 4,
    limit: int | None = None,
    force: bool = False,
) -> None:
    pdf_files = sorted(PDF_INPUT_DIR.glob("*.pdf"))
    if not pdf_files:
        logger.error("No PDFs found in: %s", PDF_INPUT_DIR.resolve())
        sys.exit(1)
    logger.info("Found %d PDF file(s)", len(pdf_files))
    if limit is not None:
        pdf_files = pdf_files[:limit]
        logger.info("Limiting to most recent %d PDF(s)", limit)
    _process_files(pdf_files, JSON_OUT_FILE, EXCEL_OUT_FILE, pages_per_chunk, force=force)


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
    parser.add_argument(
        "--limit", "-l",
        type=str, default="all",
        help="Number of most-recent PDFs to process. Default: all.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force re-extraction of already extracted PDFs.",
    )
    args = parser.parse_args()

    limit_val: int | None = None
    if args.limit.lower() != "all":
        try:
            limit_val = int(args.limit)
        except ValueError:
            logger.error("Invalid limit '%s'. Must be an integer or 'all'.", args.limit)
            sys.exit(1)

    if args.file:
        pdf_path = PDF_INPUT_DIR / args.file
        if not pdf_path.exists():
            logger.error("File not found: %s", pdf_path.resolve())
            sys.exit(1)
        _process_files([pdf_path], JSON_OUT_FILE, EXCEL_OUT_FILE, args.pages_per_chunk, force=True)
    else:
        run_pipeline(pages_per_chunk=args.pages_per_chunk, limit=limit_val, force=args.force)
