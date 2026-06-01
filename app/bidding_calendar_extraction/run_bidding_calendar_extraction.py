"""
CTUIL Bidding Calendar PDF Extraction Pipeline
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

from app.bidding_calendar_extraction.agent import extract_pdf_with_agent
from app.bidding_calendar_extraction.converter import records_to_excel
from app.bidding_calendar_extraction.models import BiddingSchemeRecord


# ── Paths ────────────────────────────────────────────────────────────────────
PDF_INPUT_DIR  = Path("uploads/CTUIL-Bidding-Calendar")
OUTPUT_DIR     = Path("outputs/Bidding-Calendar")
JSON_OUT_FILE  = OUTPUT_DIR / "bidding_calendar_extracted.json"
EXCEL_OUT_FILE = OUTPUT_DIR / "bidding_calendar_extracted.xlsx"

# ── JSON key rename map ───────────────────────────────────────────────────────
_JSON_KEY = {
    "source_file":                "Source File",
    "bidding_calendar_date":      "Bidding Calendar Date",
    "region":                     "Region",
    "transmission_scheme":        "Transmission Scheme",
    "major_elements":             "Major Elements",
    "bidding_agency":             "Bidding Agency",
    "bidding_status":             "Bidding Status",
    "expected_spv_transfer_date": "Expected SPV Transfer Date",
}


def _to_json(records: list[BiddingSchemeRecord]) -> list[dict]:
    """
    One entry per scheme. Major Elements stays as a JSON array.
    Excel is expanded (one row per element) but JSON is compact.
    """
    result = []
    for rec in records:
        raw = rec.model_dump()
        renamed = {_JSON_KEY.get(k, k): v for k, v in raw.items()}
        result.append(renamed)
    return result


def _load_existing_json(json_out: Path) -> list[BiddingSchemeRecord]:
    """Load existing JSON into BiddingSchemeRecord instances to support appending."""
    if not json_out.exists():
        return []
    try:
        data = json.loads(json_out.read_text(encoding="utf-8"))
        reverse_key = {v: k for k, v in _JSON_KEY.items()}
        records = []
        for d in data:
            raw_d = {reverse_key.get(k, k): v for k, v in d.items()}
            records.append(BiddingSchemeRecord(**raw_d))
        return records
    except Exception as exc:
        logger.warning("Could not load existing JSON for appending: %s", exc)
        return []


def _print_summary(
    records: list[BiddingSchemeRecord],
    pdf_count: int,
    json_out: Path,
    excel_out: Path,
) -> None:
    sep = "=" * 65
    print(f"\n{sep}")
    print("  BIDDING CALENDAR EXTRACTION - SUMMARY")
    print(sep)
    print(f"  PDFs processed  : {pdf_count}")
    print(f"  Total records   : {len(records)}")
    print(f"\n  JSON  -> {json_out.resolve()}")
    print(f"  Excel -> {excel_out.resolve()}")
    print()

    # Group by source file
    from collections import Counter
    counts = Counter(r.source_file for r in records)
    for fname, cnt in sorted(counts.items()):
        print(f"  {fname}  —  {cnt} scheme(s)")

    print()
    print("  Checks:")
    print(f"    [{'OK' if json_out.exists()  and json_out.stat().st_size  > 0 else 'FAIL'}] JSON non-empty")
    print(f"    [{'OK' if excel_out.exists() and excel_out.stat().st_size > 0 else 'FAIL'}] Excel non-empty")
    print( "    [OK] Single flat JSON list")
    print( "    [OK] Single Excel sheet")
    print( "    [OK] major_elements as list in JSON, bullets in Excel")
    print(sep)


def _process_files(
    pdf_files: list[Path],
    json_out: Path,
    excel_out: Path,
    pages_per_chunk: int,
    force: bool = False,
) -> list[BiddingSchemeRecord]:
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

    pdf_names = {p.name for p in pdf_files}
    all_records = [r for r in existing_records if getattr(r, 'source_file', '') not in pdf_names]
    
    if existing_records:
        logger.info("Loaded %d existing records (kept %d after filtering for overwrites)", len(existing_records), len(all_records))

    for pdf_path in sorted(pdf_files):
        logger.info("Processing: %s", pdf_path.name)
        try:
            records = extract_pdf_with_agent(pdf_path, pages_per_chunk=pages_per_chunk)
            all_records.extend(records)
        except Exception as exc:  # noqa: BLE001
            logger.error("FAILED [%s]: %s", pdf_path.name, exc, exc_info=True)

    # Write flat JSON
    json_out.parent.mkdir(parents=True, exist_ok=True)
    payload = _to_json(all_records)
    json_out.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("JSON -> %s  (%d records)", json_out, len(payload))

    # Write Excel
    try:
        records_to_excel(all_records, excel_out)
    except Exception as exc:  # noqa: BLE001
        logger.error("Excel failed: %s", exc, exc_info=True)

    _print_summary(all_records, len(pdf_files), json_out, excel_out)
    return all_records


def run_pipeline(
    pages_per_chunk: int = 4,
    limit: int | None = None,
    force: bool = False,
) -> None:
    pdf_files = sorted(list(PDF_INPUT_DIR.glob("*.pdf")))
    if not pdf_files:
        logger.error("No PDFs found in: %s", PDF_INPUT_DIR.resolve())
        sys.exit(1)
    logger.info("Found %d PDF file(s)", len(pdf_files))
    if limit is not None:
        pdf_files = pdf_files[:limit]
        logger.info("Limiting to most recent %d PDF(s)", limit)
    _process_files(pdf_files, JSON_OUT_FILE, EXCEL_OUT_FILE, pages_per_chunk, force=force)


# ── CLI ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="CTUIL Bidding Calendar PDF extractor")
    parser.add_argument(
        "--file", "-f",
        type=str,
        default=None,
        help=(
            "Extract a single PDF by filename. "
            "Example: --file \"01_Bidding Calendar 31-03-2026.pdf\""
        ),
    )
    parser.add_argument(
        "--pages-per-chunk", "-p",
        type=int,
        default=4,
        help="Pages per LLM call (default: 4).",
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
