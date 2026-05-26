"""
One-off converter to clean JSON artifacts and rebuild Bidding Calendar Excel.
Can be deleted after use.
"""

import json
import logging
import re
from pathlib import Path

from app.bidding_calendar_extraction.converter import records_to_excel
from app.bidding_calendar_extraction.models import BiddingSchemeRecord

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("clean_json_to_excel")

# Candidates for source JSON files
JSON_CANDIDATES = [
    Path("bidding.json"),
    Path("outputs/Bidding-Calendar/bidding_calendar_extracted.json"),
    Path("bidding_calendar_extracted.json"),
]

# Output path
EXCEL_OUT = Path("outputs/Bidding-Calendar/bidding_calendar_extracted.xlsx")

# Inverse key mapping from models to JSON labels
_REVERSE_KEY = {
    "Source File": "source_file",
    "Bidding Calendar Date": "bidding_calendar_date",
    "Region": "region",
    "Transmission Scheme": "transmission_scheme",
    "Major Elements": "major_elements",
    "Bidding Agency": "bidding_agency",
    "Bidding Status": "bidding_status",
    "Expected SPV Transfer Date": "expected_spv_transfer_date",
}


def clean_string(val: any) -> any:
    """Sanitize string by removing nulls, converting tab-6 artifacts, and removing tabs."""
    if not isinstance(val, str):
        return val
    # 1. Replace the weird "\t6" sequence with a clean en-dash "–"
    val = val.replace("\t6", "–")
    # 2. Replace tabs with a space
    val = val.replace("\t", " ")
    # 3. Remove null characters
    val = val.replace("\u0000", "")
    # 4. Collapse multiple spaces
    val = re.sub(r" +", " ", val)
    return val.strip()


def clean_list(val_list: list) -> list:
    """Sanitize lists of strings (like Major Elements)."""
    cleaned = []
    for item in val_list:
        cleaned_item = clean_string(item)
        if cleaned_item:  # Filter out empty/null/whitespace-only elements
            cleaned.append(cleaned_item)
    return cleaned


def clean_record_dict(record: dict) -> dict:
    """Sanitize all fields in a record dictionary."""
    cleaned = {}
    for k, v in record.items():
        if isinstance(v, list):
            cleaned[k] = clean_list(v)
        else:
            cleaned[k] = clean_string(v)
    return cleaned


def main():
    json_path = None
    for cand in JSON_CANDIDATES:
        if cand.exists():
            json_path = cand
            break

    if not json_path:
        logger.error(
            "No source JSON found! Tried paths: %s",
            [str(p.resolve()) for p in JSON_CANDIDATES],
        )
        return

    logger.info("Found source JSON: %s", json_path.resolve())

    # 1. Read raw JSON
    logger.info("Loading records from %s...", json_path.name)
    with open(json_path, "r", encoding="utf-8") as f:
        raw_data = json.load(f)

    # 2. Clean data
    logger.info("Cleaning encoding artifacts (\\u0000, \\t, \\t6) from %d records...", len(raw_data))
    cleaned_data = []
    records = []
    
    for idx, item in enumerate(raw_data, 1):
        try:
            # Deep clean the dictionary
            cleaned_item = clean_record_dict(item)
            cleaned_data.append(cleaned_item)

            # Map JSON labels back to Pydantic model fields
            model_dict = {}
            for label, value in cleaned_item.items():
                field_name = _REVERSE_KEY.get(label, label)
                model_dict[field_name] = value

            # Convert to BiddingSchemeRecord
            rec = BiddingSchemeRecord(**model_dict)
            records.append(rec)
        except Exception as e:
            logger.error("Failed to parse/clean record #%d: %s (Record: %s)", idx, e, item)
            return

    # 3. Save the clean JSON back to disk
    logger.info("Saving fully sanitized JSON back to %s...", json_path.name)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(cleaned_data, f, ensure_ascii=False, indent=2)

    # 4. Generate the Excel file
    logger.info("Generating sanitized Excel at %s...", EXCEL_OUT.resolve())
    try:
        records_to_excel(records, EXCEL_OUT)
        print("\n" + "=" * 65)
        print("  CLEANING & CONVERSION COMPLETED SUCCESSFULLY!")
        print("=" * 65)
        print(f"  Sanitized JSON : {json_path.resolve()}")
        print(f"  Generated Excel: {EXCEL_OUT.resolve()}")
        print("=" * 65 + "\n")
    except Exception as e:
        logger.exception("Failed to write Excel file: %s", e)


if __name__ == "__main__":
    main()
