"""CLI entry point for NCT extraction.

Usage:
    python -m nct_extraction uploads/CEA-NCT-Minutes/01_40th_NCT_MoM.pdf
    python -m nct_extraction uploads/CEA-NCT-Minutes/
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
from nct_extraction.reporting import build_report
from nct_extraction.to_excel import write_excel
from nct_extraction.seed_extractor import (
    build_seed_registry, load_registry, _DEFAULT_REGISTRY_PATH
)


def process_single(pdf_path: str, output_dir: str, seed_registry: dict | None = None) -> dict | None:
    """Process one PDF and save its JSON."""
    os.makedirs(output_dir, exist_ok=True)
    filename = os.path.basename(pdf_path)
    json_name = filename.replace(".pdf", ".json")
    json_path = os.path.join(output_dir, json_name)

    try:
        result = extract_from_pdf(pdf_path, seed_registry=seed_registry)
        report = build_report(result.meeting_name, result.source_pdf, result.elements)
        data = report.to_output_dict()

        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        print(f"\n[OK] {len(result.elements)} elements -> {json_path}")
        return data
    except Exception as e:
        print(f"\n[FAIL] {filename}: {e}")
        return None


def process_directory(dir_path: str, output_dir: str):
    """Process all PDFs in a directory."""
    individual_dir = os.path.join(output_dir, "individual_results")
    os.makedirs(individual_dir, exist_ok=True)

    pdfs = sorted(f for f in os.listdir(dir_path) if f.endswith(".pdf"))
    total = len(pdfs)
    print(f"Found {total} PDFs in {dir_path}\n")

    # ── Phase 1: Build seed registry ──
    print("=" * 60)
    print("PHASE 1: Building Seed Registry (backward-chaining)")
    print("=" * 60)
    seed_registry = build_seed_registry(dir_path, _DEFAULT_REGISTRY_PATH)
    total_seeds = sum(len(v) for v in seed_registry.values())
    print(f"Seed registry: {total_seeds} scheme names across {len(seed_registry)} meetings")
    print("=" * 60 + "\n")

    results = []
    log_lines = []

    for i, filename in enumerate(pdfs, 1):
        pdf_path = os.path.join(dir_path, filename)
        json_name = filename.replace(".pdf", ".json")
        json_path = os.path.join(individual_dir, json_name)

        # Skip cached results with data
        if os.path.exists(json_path):
            with open(json_path, "r", encoding="utf-8") as f:
                cached = json.load(f)
            if cached.get("elements"):
                print(f"[{i}/{total}] Skip {filename} (cached, {len(cached['elements'])} elements)")
                results.append(cached)
                continue

        print(f"[{i}/{total}] {filename}")
        t0 = time.time()

        try:
            result = extract_from_pdf(pdf_path, seed_registry=seed_registry)
            report = build_report(result.meeting_name, result.source_pdf, result.elements)
            data = report.to_output_dict()
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            results.append(data)
            elapsed = time.time() - t0
            log_lines.append(f"OK: {filename} ({len(result.elements)} elements) {elapsed:.1f}s")
        except Exception as e:
            elapsed = time.time() - t0
            log_lines.append(f"FAIL: {filename} {elapsed:.1f}s: {e}")
            print(f"  ERROR: {e}")

        time.sleep(2)  # Rate limit

    # Master JSON
    master_json = os.path.join(output_dir, "nct_extraction_master.json")
    with open(master_json, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"\nMaster JSON: {master_json}")

    # Master Excel
    master_xlsx = os.path.join(output_dir, "nct_extraction_master.xlsx")
    write_excel(results, master_xlsx)
    print(f"Master Excel: {master_xlsx}")

    # Log
    log_path = os.path.join(output_dir, "extraction_log.txt")
    with open(log_path, "w", encoding="utf-8") as f:
        f.write("\n".join(log_lines))

    # Summary
    total_elements = sum(len(r.get("elements", [])) for r in results)
    files_ok = sum(1 for r in results if r.get("elements"))
    print(f"\nDone: {files_ok}/{total} files with data, {total_elements} total elements")


def main():
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python -m nct_extraction <pdf_file>")
        print("  python -m nct_extraction <directory>")
        sys.exit(1)

    target = sys.argv[1]
    output_dir = os.path.join(os.path.dirname(__file__), "output")

    if os.path.isfile(target) and target.endswith(".pdf"):
        # For single file: load existing registry or build from parent dir
        pdf_dir = os.path.dirname(target)
        registry = load_registry(_DEFAULT_REGISTRY_PATH)
        if not registry:
            print("No seed registry found. Building from PDF directory...")
            registry = build_seed_registry(pdf_dir, _DEFAULT_REGISTRY_PATH)
        process_single(target, output_dir, seed_registry=registry)
    elif os.path.isdir(target):
        process_directory(target, output_dir)
    else:
        print(f"Error: '{target}' is not a PDF file or directory")
        sys.exit(1)


if __name__ == "__main__":
    main()
