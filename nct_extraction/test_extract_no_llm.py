"""Test the keyword-filtered page extraction (NO LLM) on multiple PDFs.
Shows exactly what text would be sent to the LLM for each file.
"""
import sys
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from nct_extraction.extractor import _extract_relevant_pages, _try_camelot_tables, _build_page_context, _chunk_pages

PDF_DIR = r"d:\Projects\extraction-pipeline\uploads\CEA-NCT-Minutes"

# Test a diverse set
test_files = [
    "01_40th_NCT_MoM.pdf",     # newest format
    "03_38th_NCT_MoM.pdf",     # large, many schemes
    "21_20th_NCT_MoM.pdf",     # mid-era
    "31_10th_NCT_MoM.pdf",     # older format
    "46_2nd_NCT_MoM.pdf",      # oldest format
    "47_1st_NCT_MoM.pdf",      # very first NCT
]

for filename in test_files:
    pdf_path = os.path.join(PDF_DIR, filename)
    if not os.path.exists(pdf_path):
        print(f"SKIP: {filename} not found")
        continue
    
    print(f"\n{'='*70}")
    print(f"FILE: {filename}")
    print(f"{'='*70}")
    
    pages = _extract_relevant_pages(pdf_path)
    print(f"  Relevant pages: {len(pages)} / {pages[0]['total_pages'] if pages else '?'}")
    print(f"  Page numbers: {[p['page'] for p in pages]}")
    
    # Show keywords found per page
    for p in pages[:5]:  # First 5
        kws = ', '.join(set(p['keywords']))
        has_tbl = "TABLE" if p['has_table'] else "text-only"
        print(f"    pg{p['page']}: [{has_tbl}] {kws}")
    if len(pages) > 5:
        print(f"    ... +{len(pages)-5} more pages")
    
    # Try camelot
    table_pages = [p['page'] for p in pages if p['has_table']]
    camelot = _try_camelot_tables(pdf_path, table_pages[:3])  # Just first 3
    if camelot:
        print(f"  Camelot OK on pages: {list(camelot.keys())}")
    
    # Build chunks
    contexts = [_build_page_context(p, camelot.get(p['page'])) for p in pages]
    chunks = _chunk_pages(contexts, max_chars=6000)
    total_chars = sum(len(c) for c in chunks)
    print(f"  Chunks: {len(chunks)} ({total_chars:,} chars total)")
    
    # Show first chunk preview
    if chunks:
        first = chunks[0]
        print(f"  First chunk preview ({len(first)} chars):")
        lines = first.split('\n')
        for line in lines[:8]:
            print(f"    {line[:100]}")
        if len(lines) > 8:
            print(f"    ... ({len(lines)} lines total)")

print("\n" + "="*70)
print("Test complete.")
