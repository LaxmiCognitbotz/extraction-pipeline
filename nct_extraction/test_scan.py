"""Test: scan ALL NCT PDFs and report what scope tables we find.
No LLM calls — just pdfplumber extraction to verify coverage.
"""
import os
import pdfplumber

PDF_DIR = r"d:\Projects\extraction-pipeline\uploads\CEA-NCT-Minutes"
OUT_FILE = r"d:\Projects\extraction-pipeline\nct_extraction\output\pdf_structure_report.txt"

SCOPE_KEYWORDS = [
    "scope of", "scope of the", "scope of transmission",
    "scope of works", "estimated cost", "implementation timeline",
    "implementation time", "capacity", "ckm",
]

TABLE_HEADER_KEYWORDS = ["scope", "scheme", "cost", "sl", "sr", "capacity", "timeline"]

def scan_pdf(pdf_path: str) -> dict:
    """Scan a single PDF and return structure info."""
    filename = os.path.basename(pdf_path)
    info = {
        "file": filename,
        "total_pages": 0,
        "has_text": False,
        "scanned_only": False,
        "scope_pages": [],
        "table_pages": [],
        "sample_headers": [],
    }
    
    with pdfplumber.open(pdf_path) as pdf:
        info["total_pages"] = len(pdf.pages)
        text_pages = 0
        
        for i, page in enumerate(pdf.pages):
            text = page.extract_text() or ""
            if text.strip():
                text_pages += 1
            
            tables = page.extract_tables() or []
            real_tables = [t for t in tables if t and len(t) > 1]
            
            if real_tables:
                info["table_pages"].append(i + 1)
            
            lower = text.lower()
            has_scope_kw = any(kw in lower for kw in SCOPE_KEYWORDS)
            
            # Check table headers
            has_scope_header = False
            for table in real_tables:
                header_str = " ".join(str(c) for c in table[0] if c).lower()
                if any(kw in header_str for kw in TABLE_HEADER_KEYWORDS):
                    has_scope_header = True
                    # Capture a sample header (first 5 cols)
                    header_clean = [str(c)[:40] if c else "" for c in table[0][:6]]
                    info["sample_headers"].append({
                        "page": i + 1,
                        "header": header_clean,
                        "rows": len(table) - 1,
                    })
            
            if has_scope_kw and has_scope_header:
                info["scope_pages"].append(i + 1)
        
        info["has_text"] = text_pages > 0
        info["scanned_only"] = text_pages < info["total_pages"] * 0.3
    
    return info


def main():
    all_pdfs = sorted(f for f in os.listdir(PDF_DIR) if f.endswith(".pdf"))
    print(f"Scanning {len(all_pdfs)} PDFs...\n")
    
    results = []
    for filename in all_pdfs:
        path = os.path.join(PDF_DIR, filename)
        info = scan_pdf(path)
        results.append(info)
        
        status = "OK" if info["scope_pages"] else ("TABLES-ONLY" if info["table_pages"] else "NO TABLES")
        if info["scanned_only"]:
            status += " [SCANNED]"
        
        print(f"  {filename:45s} | {info['total_pages']:3d}pg | scope_pages={info['scope_pages'][:5]} | tables_on={info['table_pages'][:8]} | {status}")
    
    # Write detailed report
    os.makedirs(os.path.dirname(OUT_FILE), exist_ok=True)
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        f.write("NCT PDF Structure Report\n")
        f.write("=" * 80 + "\n\n")
        
        # Summary
        total = len(results)
        with_scope = sum(1 for r in results if r["scope_pages"])
        with_tables = sum(1 for r in results if r["table_pages"])
        scanned = sum(1 for r in results if r["scanned_only"])
        
        f.write(f"Total PDFs: {total}\n")
        f.write(f"With scope tables: {with_scope}\n")
        f.write(f"With any tables: {with_tables}\n")
        f.write(f"Scanned-only (no text): {scanned}\n\n")
        
        # Unique table header patterns
        f.write("=" * 80 + "\n")
        f.write("UNIQUE TABLE HEADER PATTERNS\n")
        f.write("=" * 80 + "\n\n")
        
        seen = set()
        for r in results:
            for h in r["sample_headers"]:
                key = str(h["header"])
                if key not in seen:
                    seen.add(key)
                    f.write(f"  File: {r['file']} (page {h['page']}, {h['rows']} rows)\n")
                    f.write(f"  Header: {h['header']}\n\n")
        
        # Per-file detail
        f.write("=" * 80 + "\n")
        f.write("PER-FILE DETAILS\n")
        f.write("=" * 80 + "\n\n")
        
        for r in results:
            f.write(f"--- {r['file']} ---\n")
            f.write(f"  Pages: {r['total_pages']}, Has text: {r['has_text']}, Scanned: {r['scanned_only']}\n")
            f.write(f"  Scope pages: {r['scope_pages']}\n")
            f.write(f"  Table pages: {r['table_pages']}\n")
            if r["sample_headers"]:
                f.write(f"  Headers:\n")
                for h in r["sample_headers"]:
                    f.write(f"    p{h['page']}: {h['header']} ({h['rows']} rows)\n")
            f.write("\n")
    
    print(f"\nReport saved: {OUT_FILE}")
    print(f"\nSummary: {with_scope}/{total} have scope tables, {with_tables}/{total} have any tables, {scanned} scanned-only")


if __name__ == "__main__":
    main()
