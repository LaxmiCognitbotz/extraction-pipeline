"""
CTUIL Revocation (24.6) PDF Extraction Pipeline
================================================
Entry point:
    python -m app.revocation_extraction.run_revocation_extraction

    # Single file:
    python -m app.revocation_extraction.run_revocation_extraction \\
        --file "07_Revocation upto oct25.pdf"

    # All files:
    python -m app.revocation_extraction.run_revocation_extraction

Outputs:
    outputs/Revocations-24.6/revocations_extracted.json
    outputs/Revocations-24.6/revocations_extracted.xlsx

JSON structure — flat list, one entry per row:
[
  {
    "Source File": "07_Revocation upto oct25.pdf",
    "Upto Month": "Oct'25",
    "Application ID": "1200003331",
    "LTA ID": "1200003326 (100MW) & 1200003327 (500MW)",
    "Applicant Name": "Gujarat State Electricity Corporation Limited",
    "Region": "WR",
    "Criterion for applying": "L&FC",
    ... (all other columns with exact PDF header names)
  }
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

from app.revocation_extraction.agent import extract_pdf_with_agent
from app.revocation_extraction.converter import records_to_excel
from app.revocation_extraction.models import RevocationRecord


# ── Paths ─────────────────────────────────────────────────────────────────────
PDF_INPUT_DIR  = Path("uploads/CTUIL-Revocations-PDFs/Expected Revocation Under 24.6")
OUTPUT_DIR     = Path("outputs/Revocations-24.6")
JSON_OUT_FILE  = OUTPUT_DIR / "revocations_extracted.json"
EXCEL_OUT_FILE = OUTPUT_DIR / "revocations_extracted.xlsx"

# Fixed field → human-readable label for JSON output
_FIXED_KEY = {
    "source_file":    "Source File",
    "upto_month":     "Upto Month",
    "application_id": "Application ID",
    "lta_id":         "LTA ID",
    "applicant_name": "Applicant Name",
}


def _to_json(records: list[RevocationRecord]) -> list[dict]:
    """
    Flat list — one entry per record.
    Fixed fields first (human-readable labels), then all row_data keys as-is.
    """
    result = []
    for rec in records:
        d = rec.model_dump()
        row_data: dict = d.get("row_data") or {}
        entry = {_FIXED_KEY[k]: d[k] for k in _FIXED_KEY}
        entry.update(row_data)   # adds all PDF column headers verbatim
        result.append(entry)
    return result


def _print_summary(
    records: list[RevocationRecord],
    pdf_count: int,
    json_out: Path,
    excel_out: Path,
) -> None:
    from collections import Counter
    sep = "=" * 65
    print(f"\n{sep}")
    print("  REVOCATION 24.6 EXTRACTION - SUMMARY")
    print(sep)
    print(f"  PDFs processed : {pdf_count}")
    print(f"  Total records  : {len(records)}")
    print(f"\n  JSON  -> {json_out.resolve()}")
    print(f"  Excel -> {excel_out.resolve()}")
    print()
    counts = Counter(r.source_file for r in records)
    for fname, cnt in sorted(counts.items()):
        upto = next((r.upto_month for r in records if r.source_file == fname), "?")
        print(f"  {fname}  —  {cnt} row(s)  [upto: {upto}]")
    print()
    lta_count = sum(1 for r in records if r.lta_id)
    print(f"  Rows with LTA ID : {lta_count} / {len(records)}")
    print(sep)


def _process_files(
    pdf_files: list[Path],
    json_out: Path,
    excel_out: Path,
    pages_per_chunk: int,
) -> list[RevocationRecord]:
    all_records: list[RevocationRecord] = []

    for pdf_path in sorted(pdf_files):
        logger.info("Processing: %s", pdf_path.name)
        try:
            records = extract_pdf_with_agent(pdf_path, pages_per_chunk=pages_per_chunk)
            all_records.extend(records)
        except Exception as exc:
            logger.error("FAILED [%s]: %s", pdf_path.name, exc, exc_info=True)

    json_out.parent.mkdir(parents=True, exist_ok=True)
    payload = _to_json(all_records)
    json_out.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("JSON -> %s  (%d records)", json_out, len(payload))

    try:
        records_to_excel(all_records, excel_out)
    except Exception as exc:
        logger.error("Excel failed: %s", exc, exc_info=True)

    _print_summary(all_records, len(pdf_files), json_out, excel_out)
    return all_records


def run_pipeline(pages_per_chunk: int = 3) -> None:
    pdf_files = list(PDF_INPUT_DIR.glob("*.pdf"))
    if not pdf_files:
        logger.error("No PDFs found in: %s", PDF_INPUT_DIR.resolve())
        sys.exit(1)
    logger.info("Found %d PDF file(s)", len(pdf_files))
    _process_files(pdf_files, JSON_OUT_FILE, EXCEL_OUT_FILE, pages_per_chunk)


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="CTUIL Revocation 24.6 PDF extractor")
    parser.add_argument(
        "--file", "-f",
        type=str, default=None,
        help="Single PDF filename inside the input directory.",
    )
    parser.add_argument(
        "--pages-per-chunk", "-p",
        type=int, default=3,
        help="Pages per LLM call (default: 3).",
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
