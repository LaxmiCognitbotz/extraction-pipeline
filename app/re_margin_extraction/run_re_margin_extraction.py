"""
CTUIL Renewable Energy Margin PDF Extraction Pipeline CLI.
Scans and extracts all 3 categories of Margin PDFs into structured JSONs and a 3-sheet Excel.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("re_margin_pipeline")

from app.re_margin_extraction.agent import extract_margin_pdf
from app.re_margin_extraction.converter import margins_to_excel

# ── Paths ────────────────────────────────────────────────────────────────────
INPUT_ROOT = Path("uploads/CTUIL-Renewable-Energy/Margin")
NON_RE_DIR = INPUT_ROOT / "Non-RE"
PROPOSED_RE_DIR = INPUT_ROOT / "Proposed RE"
RE_DIR = INPUT_ROOT / "RE Substations"

OUTPUT_DIR = Path("outputs/RE-Margin")
NON_RE_JSON_OUT = OUTPUT_DIR / "non_re_margin_extracted.json"
PROPOSED_RE_JSON_OUT = OUTPUT_DIR / "proposed_re_margin_extracted.json"
RE_JSON_OUT = OUTPUT_DIR / "re_margin_extracted.json"
EXCEL_OUT = OUTPUT_DIR / "renewable_energy_margin_extracted.xlsx"


def _to_flat_json(records: list[Any]) -> list[dict]:
    """Convert a list of Pydantic model records to standard serialized dictionaries."""
    return [rec.model_dump(by_alias=True) for rec in records]


def _process_folder(
    folder_path: Path,
    kind: str,
    json_out: Path,
    limit: int | None,
    pages_per_chunk: int,
    force: bool = False,
) -> list[Any]:
    """Extract margin records from all PDFs in a given folder up to the limit."""
    all_records = []
    if not folder_path.exists():
        logger.warning("Directory does not exist: %s", folder_path.resolve())
        return []

    pdf_files = sorted(list(folder_path.glob("*.pdf")))
    if not pdf_files:
        logger.warning("No PDFs found in: %s", folder_path.resolve())
        return []

    logger.info("Found %d PDF(s) in %s", len(pdf_files), folder_path.name)
    if limit is not None:
        pdf_files = pdf_files[:limit]  # Process the most recent files (top of the sorted list)
        logger.info("Limiting to the most recent %d PDF(s) for extraction", len(pdf_files))

    existing_records = _load_existing_records(json_out, kind)
    
    if not force:
        already_processed = {getattr(r, 'source_file', '') for r in existing_records}
        skipped = [p for p in pdf_files if p.name in already_processed]
        pdf_files = [p for p in pdf_files if p.name not in already_processed]
        if skipped:
            logger.info("Skipping %d already-extracted PDF(s) in %s. Use --force to re-extract.", len(skipped), folder_path.name)
        if not pdf_files:
            logger.info("All selected PDFs in %s are already extracted. Exiting.", folder_path.name)
            return existing_records

    pdf_names = {p.name for p in pdf_files}
    all_records = [r for r in existing_records if getattr(r, 'source_file', '') not in pdf_names]
    
    if existing_records:
        logger.info("Loaded %d existing records (kept %d after filtering for overwrites)", len(existing_records), len(all_records))

    for pdf_path in pdf_files:
        logger.info("Starting extraction for PDF: %s", pdf_path.name)
        try:
            records = extract_margin_pdf(pdf_path, kind=kind, pages_per_chunk=pages_per_chunk)
            all_records.extend(records)
        except Exception as exc:
            logger.error("FAILED to process [%s]: %s", pdf_path.name, exc, exc_info=True)

    # Save to JSON
    json_out.parent.mkdir(parents=True, exist_ok=True)
    payload = _to_flat_json(all_records)
    with open(json_out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    logger.info("JSON saved: %s (%d record(s))", json_out, len(payload))

    return all_records


def _print_summary(
    non_re_count: int,
    proposed_re_count: int,
    re_count: int,
) -> None:
    sep = "=" * 70
    print(f"\n{sep}")
    print("  RENEWABLE ENERGY MARGIN EXTRACTION - COMPLETE SUMMARY")
    print(sep)
    print(f"  Non-RE Substation Records      : {non_re_count}")
    print(f"  Proposed RE Substation Records : {proposed_re_count}")
    print(f"  RE Substation Records          : {re_count}")
    print(f"  Total Records Extracted        : {non_re_count + proposed_re_count + re_count}")
    print(f"\n  JSON Output Directory          : {OUTPUT_DIR.resolve()}")
    print(f"  Final Styled Excel (3 sheets)  : {EXCEL_OUT.resolve()}")
    print(sep)
    print("  Sheets Created in Excel:")
    print("    [1] 'Non-RE Substations'      - Ocean Breeze Theme")
    print("    [2] 'Proposed RE Substations' - Royal Plum Theme")
    print("    [3] 'RE Substations'          - Forest Mint Theme")
    print(sep + "\n")


def _load_existing_records(json_path: Path, kind: str) -> list[Any]:
    """Load existing extracted records from JSON file to preserve them in Excel."""
    if not json_path.exists():
        return []
    
    logger.info("Loading existing records from JSON to preserve sheet data: %s", json_path.name)
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        
        records = []
        for item in raw:
            if kind == "non-re":
                from app.re_margin_extraction.models import NonRESubstationMarginRecord
                records.append(NonRESubstationMarginRecord(**item))
            elif kind == "proposed-re":
                from app.re_margin_extraction.models import ProposedRESubstationMarginRecord
                records.append(ProposedRESubstationMarginRecord(**item))
            elif kind == "re-substations":
                from app.re_margin_extraction.models import RESubstationMarginRecord
                records.append(RESubstationMarginRecord(**item))
        return records
    except Exception as exc:
        logger.warning("Failed to load existing records from %s: %s", json_path.name, exc)
        return []


def run_pipeline(
    limit: int | None = 1,
    pages_per_chunk: int = 4,
    folder_filter: str = "all",
    single_file: str | None = None,
    force: bool = False,
) -> None:
    """Run extraction on folders independently and export a styled Excel file."""
    logger.info("Initializing Renewable Energy Margin Extraction Pipeline...")
    
    non_re_records = []
    proposed_re_records = []
    re_records = []

    # Auto-detect folder kind if single_file is provided
    detected_kind = None
    target_pdf_path = None
    if single_file:
        for k, folder in [("non-re", NON_RE_DIR), ("proposed-re", PROPOSED_RE_DIR), ("re-substations", RE_DIR)]:
            candidate = folder / single_file
            if candidate.exists():
                target_pdf_path = candidate
                detected_kind = k
                break
        
        if not target_pdf_path:
            logger.error("File '%s' not found in any subfolder under %s", single_file, INPUT_ROOT)
            sys.exit(1)
        logger.info("Detected single file for extraction: %s (%s)", target_pdf_path.name, detected_kind.upper())

    # 1. Process Non-RE Substations
    should_extract_non_re = (
        (folder_filter == "all" or folder_filter == "non-re")
        and (not single_file or detected_kind == "non-re")
    )
    if should_extract_non_re:
        logger.info("=== [1/3] Extracting Non-RE Substations Margin ===")
        if single_file:
            existing = _load_existing_records(NON_RE_JSON_OUT, kind="non-re")
            non_re_records = [r for r in existing if getattr(r, 'source_file', '') != target_pdf_path.name]
            
            recs = extract_margin_pdf(target_pdf_path, kind="non-re", pages_per_chunk=pages_per_chunk)
            non_re_records.extend(recs)
            
            NON_RE_JSON_OUT.parent.mkdir(parents=True, exist_ok=True)
            payload = _to_flat_json(non_re_records)
            with open(NON_RE_JSON_OUT, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            logger.info("JSON saved: %s (%d record(s))", NON_RE_JSON_OUT, len(payload))
        else:
            non_re_records = _process_folder(
                NON_RE_DIR,
                kind="non-re",
                json_out=NON_RE_JSON_OUT,
                limit=limit,
                pages_per_chunk=pages_per_chunk,
                force=force
            )
    else:
        non_re_records = _load_existing_records(NON_RE_JSON_OUT, kind="non-re")

    # 2. Process Proposed RE Substations (older reports)
    should_extract_proposed_re = (
        (folder_filter == "all" or folder_filter == "proposed-re")
        and (not single_file or detected_kind == "proposed-re")
    )
    if should_extract_proposed_re:
        logger.info("=== [2/3] Extracting Proposed RE Substations Margin ===")
        if single_file:
            existing = _load_existing_records(PROPOSED_RE_JSON_OUT, kind="proposed-re")
            proposed_re_records = [r for r in existing if getattr(r, 'source_file', '') != target_pdf_path.name]
            
            recs = extract_margin_pdf(target_pdf_path, kind="proposed-re", pages_per_chunk=pages_per_chunk)
            proposed_re_records.extend(recs)
            
            PROPOSED_RE_JSON_OUT.parent.mkdir(parents=True, exist_ok=True)
            payload = _to_flat_json(proposed_re_records)
            with open(PROPOSED_RE_JSON_OUT, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            logger.info("JSON saved: %s (%d record(s))", PROPOSED_RE_JSON_OUT, len(payload))
        else:
            proposed_re_records = _process_folder(
                PROPOSED_RE_DIR,
                kind="proposed-re",
                json_out=PROPOSED_RE_JSON_OUT,
                limit=limit,
                pages_per_chunk=pages_per_chunk,
                force=force
            )
    else:
        proposed_re_records = _load_existing_records(PROPOSED_RE_JSON_OUT, kind="proposed-re")

    # 3. Process RE Substations
    should_extract_re = (
        (folder_filter == "all" or folder_filter == "re-substations")
        and (not single_file or detected_kind == "re-substations")
    )
    if should_extract_re:
        logger.info("=== [3/3] Extracting RE Substations Margin ===")
        if single_file:
            existing = _load_existing_records(RE_JSON_OUT, kind="re-substations")
            re_records = [r for r in existing if getattr(r, 'source_file', '') != target_pdf_path.name]
            
            recs = extract_margin_pdf(target_pdf_path, kind="re-substations", pages_per_chunk=pages_per_chunk)
            re_records.extend(recs)
            
            RE_JSON_OUT.parent.mkdir(parents=True, exist_ok=True)
            payload = _to_flat_json(re_records)
            with open(RE_JSON_OUT, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            logger.info("JSON saved: %s (%d record(s))", RE_JSON_OUT, len(payload))
        else:
            re_records = _process_folder(
                RE_DIR,
                kind="re-substations",
                json_out=RE_JSON_OUT,
                limit=limit,
                pages_per_chunk=pages_per_chunk,
                force=force
            )
    else:
        re_records = _load_existing_records(RE_JSON_OUT, kind="re-substations")

    # 4. Generate 3-sheet Excel
    logger.info("Generating fully formatted 3-sheet Excel workbook...")
    try:
        margins_to_excel(
            non_re_records=non_re_records,
            proposed_re_records=proposed_re_records,
            re_records=re_records,
            output_path=EXCEL_OUT
        )
    except Exception as exc:
        logger.exception("Excel generation failed: %s", exc)

    _print_summary(len(non_re_records), len(proposed_re_records), len(re_records))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="CTUIL Renewable Energy Margin PDF Extractor")
    parser.add_argument(
        "--folder", "-f",
        type=str,
        default="all",
        choices=["all", "non-re", "proposed-re", "re-substations"],
        help="Specify which margin subfolder category to extract. Default is 'all'."
    )
    parser.add_argument(
        "--file", "-file",
        type=str,
        default=None,
        help="Extract a single PDF file by filename. Auto-detects category folder. Example: -file \"01_SS Margin 31 08 2025.pdf\""
    )
    parser.add_argument(
        "--limit", "-l",
        type=str,
        default="all",
        help="Number of PDFs to extract from the selected subfolder(s) (most recent first). Set to 'all' for everything. Default is 'all'."
    )
    parser.add_argument(
        "--pages-per-chunk", "-p",
        type=int,
        default=4,
        help="Pages per LLM call (default: 4)."
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force re-extraction of already extracted PDFs."
    )
    args = parser.parse_args()

    # Parse limit argument
    limit_val = None  # None means extract ALL PDFs
    if args.limit.lower() != "all":
        try:
            limit_val = int(args.limit)
        except ValueError:
            logger.error("Invalid limit value: '%s'. Must be an integer or 'all'.", args.limit)
            sys.exit(1)

    run_pipeline(
        limit=limit_val,
        pages_per_chunk=args.pages_per_chunk,
        folder_filter=args.folder,
        single_file=args.file,
        force=args.force
    )
