"""Batch runner: process all NCT PDFs → individual JSONs → master JSON → Excel.

Usage:
    python nct_extraction/main.py
"""

import json
import os
import sys
import time
from pathlib import Path

# Ensure project root is importable
project_root = str(Path(__file__).parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from nct_extraction.extractor import extract_from_pdf
from nct_extraction.to_excel import write_excel


def main():
    input_dir = Path(r"d:\Projects\extraction-pipeline\uploads\CEA-NCT-Minutes")
    output_dir = Path(r"d:\Projects\extraction-pipeline\nct_extraction\output")
    individual_dir = output_dir / "individual_results"

    individual_dir.mkdir(parents=True, exist_ok=True)

    log_file = output_dir / "extraction_log.txt"
    master_json = output_dir / "nct_extraction_master.json"
    master_excel = output_dir / "nct_extraction_master.xlsx"

    all_pdfs = sorted(f for f in os.listdir(input_dir) if f.endswith(".pdf"))
    total = len(all_pdfs)
    print(f"Total PDFs to process: {total}")

    results = []

    with open(log_file, "w", encoding="utf-8") as log:
        for i, filename in enumerate(all_pdfs, 1):
            pdf_path = str(input_dir / filename)
            json_name = filename.replace(".pdf", ".json")
            json_path = individual_dir / json_name

            # Skip already-processed files (with >0 elements)
            if json_path.exists():
                with open(json_path, "r", encoding="utf-8") as f:
                    cached = json.load(f)
                if cached.get("elements"):
                    print(f"[{i}/{total}] Skipping {filename} (cached, {len(cached['elements'])} elements)")
                    results.append(cached)
                    continue
                # Re-process files that returned 0 elements
                print(f"[{i}/{total}] Re-processing {filename} (was 0 elements)")

            print(f"[{i}/{total}] Processing {filename}...")
            start_time = time.time()

            max_retries = 3
            success = False
            for attempt in range(max_retries):
                try:
                    data = extract_from_pdf(pdf_path)
                    data_dict = data.model_dump()

                    # Save individual result
                    with open(json_path, "w", encoding="utf-8") as f:
                        json.dump(data_dict, f, indent=2, ensure_ascii=False)

                    results.append(data_dict)
                    elapsed = time.time() - start_time
                    msg = f"SUCCESS: {filename} ({len(data.elements)} elements) in {elapsed:.1f}s"
                    print(f"  {msg}")
                    log.write(msg + "\n")
                    log.flush()
                    success = True
                    break
                except Exception as e:
                    err_str = str(e).lower()
                    is_transient = any(
                        x in err_str
                        for x in ["503", "429", "timeout", "connection", "retry"]
                    )
                    if is_transient and attempt < max_retries - 1:
                        wait_time = (2 ** attempt) * 15
                        print(f"  Attempt {attempt+1} failed. Retrying in {wait_time}s...")
                        time.sleep(wait_time)
                        continue
                    else:
                        elapsed = time.time() - start_time
                        msg = f"ERROR: {filename} after {elapsed:.1f}s: {e}"
                        print(f"  {msg}")
                        log.write(msg + "\n")
                        log.flush()
                        break

            if not success and attempt == max_retries - 1:
                msg = f"FAILED: {filename} after {max_retries} retries"
                log.write(msg + "\n")
                log.flush()

            # Small delay between files
            time.sleep(2)

    # ── Write master JSON ──
    with open(master_json, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"\nMaster JSON saved: {master_json}")

    # ── Write master Excel ──
    write_excel(results, str(master_excel))
    print(f"Master Excel saved: {master_excel}")

    # ── Summary ──
    total_elements = sum(len(r.get("elements", [])) for r in results)
    files_with_data = sum(1 for r in results if r.get("elements"))
    print(f"\nDone: {files_with_data}/{total} files yielded data, {total_elements} total elements")


if __name__ == "__main__":
    main()
