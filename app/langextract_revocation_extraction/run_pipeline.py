"""
CTUIL Revocation 24.6 — LangExtract Extraction Pipeline CLI.

Processes each PDF one at a time (page-by-page), extracting every revocation
row using Google's `langextract` library routed through the project's Azure
OpenAI VM endpoint (llm_client.bat).

Usage examples:
  # Process all PDFs in the default folder
  python -m app.langextract_revocation_extraction.run_pipeline

  # Process a single file
  python -m app.langextract_revocation_extraction.run_pipeline --file "24.6 List upto Jun25.pdf"

  # Limit to first 2 PDFs
  python -m app.langextract_revocation_extraction.run_pipeline --limit 2
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("langextract_revocation_pipeline")

from app.langextract_revocation_extraction.pdf_reader import read_pdf_page_bundles
from app.langextract_revocation_extraction.extractor import extract_pdf

# ── I/O paths ────────────────────────────────────────────────────────────────
INPUT_DIR = Path("uploads/CTUIL-Revocations-PDFs")
OUTPUT_DIR = Path("outputs/LangExtract-Revocations")
OUTPUT_JSON = OUTPUT_DIR / "revocations_langextract.json"


# ── Pipeline ─────────────────────────────────────────────────────────────────

def run_pipeline(
    limit: int | None = None,
    single_file: str | None = None,
) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Collect PDFs
    if single_file:
        pdf_path = INPUT_DIR / single_file
        if not pdf_path.exists():
            logger.error("File not found: %s", pdf_path.resolve())
            sys.exit(1)
        pdf_files = [pdf_path]
    else:
        if not INPUT_DIR.exists():
            logger.error("Input directory does not exist: %s", INPUT_DIR.resolve())
            sys.exit(1)
        pdf_files = sorted(INPUT_DIR.glob("*.pdf"))
        if not pdf_files:
            logger.warning("No PDFs found in %s", INPUT_DIR.resolve())
            return
        if limit is not None:
            pdf_files = pdf_files[:limit]

    logger.info("Processing %d PDF(s) via langextract…", len(pdf_files))

    existing_rows: list[dict] = []
    if OUTPUT_JSON.exists():
        try:
            with open(OUTPUT_JSON, "r", encoding="utf-8") as fh:
                existing_rows = json.load(fh)
        except Exception as exc:
            logger.warning("Failed to load existing JSON: %s", exc)
            
    pdf_names = {p.name for p in pdf_files}
    all_rows = [r for r in existing_rows if r.get("source_file") not in pdf_names]
    
    if existing_rows:
        logger.info("Loaded %d existing records (kept %d after filtering for overwrites)", len(existing_rows), len(all_rows))

    for pdf_path in pdf_files:
        logger.info("─── Processing: %s ───", pdf_path.name)
        try:
            page_bundles = read_pdf_page_bundles(pdf_path)
            rows = extract_pdf(pdf_path, page_bundles)
            all_rows.extend(rows)
            logger.info("  → %d row(s) from %s", len(rows), pdf_path.name)
        except Exception as exc:
            logger.error("FAILED [%s]: %s", pdf_path.name, exc, exc_info=True)

    # Save JSON
    with open(OUTPUT_JSON, "w", encoding="utf-8") as fh:
        json.dump(all_rows, fh, ensure_ascii=False, indent=2)

    _print_summary(len(pdf_files), len(all_rows))


def _print_summary(num_pdfs: int, num_rows: int) -> None:
    sep = "=" * 65
    print(f"\n{sep}")
    print("  LANGEXTRACT REVOCATION PIPELINE — COMPLETE")
    print(sep)
    print(f"  PDFs processed          : {num_pdfs}")
    print(f"  Total rows extracted    : {num_rows}")
    print(f"  Output JSON             : {OUTPUT_JSON.resolve()}")
    print(sep + "\n")


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="CTUIL Revocation 24.6 — LangExtract Extraction Pipeline"
    )
    parser.add_argument(
        "--file", "-f",
        type=str,
        default=None,
        help='Extract a single PDF by filename. E.g. --file "24.6 List upto Jun25.pdf"',
    )
    parser.add_argument(
        "--limit", "-l",
        type=int,
        default=None,
        help="Max number of PDFs to process (default: all).",
    )
    args = parser.parse_args()

    run_pipeline(limit=args.limit, single_file=args.file)
