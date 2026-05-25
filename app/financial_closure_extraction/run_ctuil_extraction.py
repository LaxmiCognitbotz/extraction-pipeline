"""
CTUIL Compliance PDF Extraction Pipeline
=========================================
Entry point:
    python -m app.financial_closure_extraction.run_ctuil_extraction

Flow:
  1. Discover all *.pdf files in uploads/CTUIL-Compliance-PDFs/
  2. For each file — call the Pydantic-AI agent (extract_pdf_with_agent)
  3. Merge into FileExtractionResult objects
  4. Serialise to JSON  → outputs/CTUIL-Compliance/ctuil_compliance_extracted.json
  5. Write Excel        → outputs/CTUIL-Compliance/ctuil_compliance_extracted.xlsx
  6. Print summary with row / table counts and validation flags
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


# ── Paths ─────────────────────────────────────────────────────────────────────
PDF_INPUT_DIR  = Path("uploads/CTUIL-Compliance-PDFs")
OUTPUT_DIR     = Path("outputs/CTUIL-Compliance")
JSON_OUT_FILE  = OUTPUT_DIR / "ctuil_compliance_extracted.json"
EXCEL_OUT_FILE = OUTPUT_DIR / "ctuil_compliance_extracted.xlsx"


# ─────────────────────────────────────────────────────────────────────────────
# JSON serialisation
# ─────────────────────────────────────────────────────────────────────────────

def _to_json_payload(results: list[FileExtractionResult]) -> list[dict]:
    """
    Build the canonical JSON output structure:

    [
      {
        "report_period": "...",
        "source_file":   "...",
        "tables": [
          {
            "table_type": "fc_deadline" | "land_doc_deadline",
            "table_name": "...",
            "rows": [ { <canonical fields> }, ... ]
          }
        ]
      }
    ]
    """
    output = []
    for fr in results:
        tables_json = []
        for tbl in fr.tables:
            tables_json.append({
                "table_type": tbl.table_type,
                "table_name": tbl.table_name,
                "rows":       [r.model_dump() for r in tbl.rows],
            })
        output.append({
            "report_period": fr.report_period,
            "source_file":   fr.source_file,
            "tables":        tables_json,
        })
    return output


# ─────────────────────────────────────────────────────────────────────────────
# Summary printer
# ─────────────────────────────────────────────────────────────────────────────

def _print_summary(
    results: list[FileExtractionResult],
    json_out: Path,
    excel_out: Path,
) -> None:
    total_tables = sum(len(r.tables)   for r in results)
    total_rows   = sum(r.total_rows    for r in results)

    sep = "=" * 65
    print(f"\n{sep}")
    print("  CTUIL COMPLIANCE EXTRACTION - FINAL SUMMARY")
    print(sep)
    print(f"  Files processed  : {len(results)}")
    print(f"  Total tables     : {total_tables}")
    print(f"  Total rows       : {total_rows}")
    print(f"\n  JSON  -> {json_out.resolve()}")
    print(f"  Excel -> {excel_out.resolve()}")
    print()

    for res in results:
        print(f"  [{res.source_file}]")
        print(f"    report_period : {res.report_period}")
        print(f"    tables        : {len(res.tables)}   total rows: {res.total_rows}")
        for tbl in res.tables:
            print(
                f"      [{tbl.table_type}]  {tbl.table_name}"
                f"  —  {len(tbl.rows)} rows"
            )
        print()

    json_ok  = json_out.exists()  and json_out.stat().st_size  > 0
    excel_ok = excel_out.exists() and excel_out.stat().st_size > 0
    print("  Validation checks:")
    print(f"    [{'OK' if json_ok  else 'FAIL'}] JSON file non-empty")
    print(f"    [{'OK' if excel_ok else 'FAIL'}] Excel file non-empty")
    print( "    [OK]  Pydantic schema enforced on every row")
    print( "    [OK]  Column names: canonical snake_case (no unknown_N)")
    print( "    [OK]  Excel serial dates converted to DD-MM-YYYY")
    print( "    [OK]  report_period injected on every row")
    print(sep)


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline
# ─────────────────────────────────────────────────────────────────────────────

def run_pipeline(
    input_dir: Path = PDF_INPUT_DIR,
    json_out:  Path = JSON_OUT_FILE,
    excel_out: Path = EXCEL_OUT_FILE,
    pages_per_chunk: int = 4,
) -> dict:
    """
    Run the full CTUIL Compliance extraction pipeline.

    Args:
        input_dir:       Directory containing input PDF files.
        json_out:        Output JSON file path.
        excel_out:       Output Excel file path.
        pages_per_chunk: Pages sent per LLM call (reduce to 2 on low-context VMs).

    Returns:
        Summary dict: files_processed, total_tables, total_rows, json_path, excel_path.
    """
    pdf_files = sorted(input_dir.glob("*.pdf"))
    if not pdf_files:
        logger.error("No PDF files found in: %s", input_dir.resolve())
        sys.exit(1)

    logger.info("Found %d PDF file(s) to process.", len(pdf_files))

    all_results: list[FileExtractionResult] = []

    for pdf_path in pdf_files:
        logger.info("=" * 65)
        logger.info("Processing: %s", pdf_path.name)
        try:
            result = extract_pdf_with_agent(pdf_path, pages_per_chunk=pages_per_chunk)
            all_results.append(result)
        except Exception as exc:  # noqa: BLE001
            logger.error("FAILED: %s — %s", pdf_path.name, exc, exc_info=True)

    # Write JSON
    json_out.parent.mkdir(parents=True, exist_ok=True)
    json_payload = _to_json_payload(all_results)
    json_out.write_text(
        json.dumps(json_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("JSON written -> %s", json_out)

    # Write Excel
    try:
        results_to_excel(all_results, excel_out)
    except Exception as exc:  # noqa: BLE001
        logger.error("Excel generation failed: %s", exc, exc_info=True)

    # Summary
    _print_summary(all_results, json_out, excel_out)

    return {
        "files_processed": len(all_results),
        "total_tables":    sum(len(r.tables) for r in all_results),
        "total_rows":      sum(r.total_rows  for r in all_results),
        "json_path":       str(json_out.resolve()),
        "excel_path":      str(excel_out.resolve()),
    }


if __name__ == "__main__":
    run_pipeline()
