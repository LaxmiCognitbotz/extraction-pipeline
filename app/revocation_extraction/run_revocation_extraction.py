"""
CTUIL Revocation (24.6) PDF Extraction Pipeline
================================================
Entry point:
    python -m app.revocation_extraction.run_revocation_extraction

    # Single file:
    python -m app.revocation_extraction.run_revocation_extraction \\
        --file "07_Revocation upto oct25.pdf"

    # Limit to N most recent PDFs:
    python -m app.revocation_extraction.run_revocation_extraction --limit 3

    # All files (default):
    python -m app.revocation_extraction.run_revocation_extraction

Outputs:
    outputs/Revocations-24.6/revocations_extracted.json
    outputs/Revocations-24.6/revocations_extracted.xlsx

JSON structure — flat list, one entry per row, with all 14 core columns:
[
  {
    "Source File": "01_Final list Jul'26.pdf",
    "Upto Month": "Jul'26",
    "Application ID": "1200003331",
    "LTA ID": "1200003326 (100MW) & 1200003327 (500MW)",
    "Applicant Name": "Gujarat State Electricity Corporation Limited",
    "Region": "WR",
    "Criterion": "L&FC",
    "Type of Project": "Solar",
    "Present Connectivity / deemed GNA (MW)": "600",
    "Substation": "KPS-2",
    "Connectivity / GNA Start Date (Firm)": "30/11/2023",
    "Connectivity Status": "Not Effective",
    "Date Connectivity / GNA Made Effective": "31-Jul-26",
    "Generation Commissioning Status": "Not commissioned",
    "SCOD as per Application": "30-Jun-23",
    "Updated / Revised SCOD": "NA",
    "24.6 Compliance Due Date": "31-Jul-26"
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
logger = logging.getLogger("revocation_pipeline")

from app.revocation_extraction.agent import extract_pdf_with_agent
from app.revocation_extraction.converter import records_to_excel, _COLUMNS
from app.revocation_extraction.models import RevocationRecord


# ── Paths ─────────────────────────────────────────────────────────────────────
PDF_INPUT_DIR  = Path("uploads/CTUIL-Revocations-PDFs/Expected Revocation Under 24.6")
OUTPUT_DIR     = Path("outputs/Revocations-24.6")
JSON_OUT_FILE  = OUTPUT_DIR / "revocations_extracted.json"
EXCEL_OUT_FILE = OUTPUT_DIR / "revocations_extracted.xlsx"

# Build label map from converter column definitions
_FIELD_TO_LABEL = {field: label for field, label, _ in _COLUMNS}


def _to_json(records: list[RevocationRecord]) -> list[dict]:
    """Flat list — one entry per record using human-readable column labels."""
    result = []
    for rec in records:
        d = rec.model_dump()
        entry = {_FIELD_TO_LABEL[field]: d.get(field) for field, _, _ in _COLUMNS}
        result.append(entry)
    return result


def _load_existing_json(json_out: Path) -> list[RevocationRecord]:
    """Load existing JSON into RevocationRecord instances if available."""
    if not json_out.exists():
        return []
    try:
        data = json.loads(json_out.read_text(encoding="utf-8"))
        label_to_field = {label: field for field, label, _ in _COLUMNS}
        records = []
        for d in data:
            raw_d = {label_to_field[k]: v for k, v in d.items() if k in label_to_field}
            records.append(RevocationRecord(**raw_d))
        return records
    except Exception as exc:
        logger.warning("Could not load existing JSON for appending: %s", exc)
        return []


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
    force: bool = False,
) -> list[RevocationRecord]:
    existing_records = _load_existing_json(json_out)
    
    if not force:
        already_processed = {getattr(r, 'source_file', '') for r in existing_records}
        skipped = [p for p in pdf_files if p.name in already_processed]
        pdf_files = [p for p in pdf_files if p.name not in already_processed]
        if skipped:
            logger.info("Skipping %d already-extracted PDF(s). Use --force to re-extract.", len(skipped))
        if not pdf_files:
            logger.info("All selected PDFs are already extracted. Exiting.")
            return existing_records

    # Remove records that come from the PDFs we are currently processing (to prevent dupes)
    pdf_names = {p.name for p in pdf_files}
    all_records = [r for r in existing_records if r.source_file not in pdf_names]
    
    if existing_records:
        logger.info("Loaded %d existing records (kept %d after filtering for overwrites)", len(existing_records), len(all_records))

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


def run_pipeline(
    pages_per_chunk: int = 3,
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
        "--limit", "-l",
        type=str, default="all",
        help="Number of most-recent PDFs to process. Default: all.",
    )
    parser.add_argument(
        "--pages-per-chunk", "-p",
        type=int, default=3,
        help="Pages per LLM call (default: 3).",
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
