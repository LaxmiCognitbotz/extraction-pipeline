"""Deep audit: go through EVERY PDF, extract ALL scope tables, verify structure.

No LLM calls. Pure pdfplumber + camelot verification.
Reports: broken headers, empty tables, misaligned columns, missing data.
"""
import os
import sys
import json
import pdfplumber
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

PDF_DIR = r"d:\Projects\extraction-pipeline\uploads\CEA-NCT-Minutes"
REPORT_FILE = r"d:\Projects\extraction-pipeline\nct_extraction\output\deep_audit_report.txt"

# Keywords that indicate a scope data page
STRONG_KW = [
    "scope of the transmission", "scope of transmission",
    "scope of works", "scope of work", "scope of the scheme",
]
WEAK_KW = [
    "estimated cost", "implementation timeline", "implementation time-frame",
    "implementation timeframe", "capacity /km", "capacity/km",
    "capacity (mva)", "capacity/ckm", "capacity /ckm",
]
TABLE_HDR_KW = ["scope", "cost", "capacity", "timeline", "timeframe",
                "implementing", "estimated", "schedule"]

# Known header patterns we expect to see (normalized)
EXPECTED_HEADERS = {
    "sl": ["sl.no.", "sl. no.", "sl.no", "s.no.", "s. no.", "si no.", "si no",
           "sr. no", "sr.no.", "sr.no", "package", "pack"],
    "scope": ["scope of the transmission scheme", "scope of works", "scope of work",
              "scope of the scheme", "scope", "scope of the transmission sche",
              "description of transmission element", "transmission work"],
    "capacity": ["capacity /km", "capacity/km", "capacity /ckm", "capacity/ckm",
                 "capacity (mva)", "capacity", "capacity (mva) / line length",
                 "item description"],
    "cost": ["estimated cost", "cost estimate", "estimated cost (rs.) cr.",
             "estimated cost (₹ crore)", "estimated cost (₹ cr)",
             "cost (rs. in crores)", "₹ crores", "cost"],
    "timeline": ["implementation timeline", "implementation time-frame",
                 "implementation timeframe", "schedule", "time-frame",
                 "timeframe", "implementation timeline (month"],
    "agency": ["implementing agency", "implementing", "bpc",
               "bid process coordinator", "tender issuing authority"],
}


def normalize_header(h):
    """Clean a header string for matching."""
    if not h:
        return ""
    return str(h).replace("\n", " ").replace("\r", " ").strip().lower()[:50]


def classify_header(header_cells):
    """Classify what columns a table header contains."""
    found = {}
    normed = [normalize_header(c) for c in header_cells]
    
    for col_type, patterns in EXPECTED_HEADERS.items():
        for i, cell in enumerate(normed):
            if any(p in cell for p in patterns):
                found[col_type] = i
                break
    
    return found, normed


def audit_table(table, page_num, table_idx):
    """Audit a single table for structural issues."""
    issues = []
    
    if not table or len(table) < 2:
        issues.append("EMPTY: < 2 rows")
        return issues, {}
    
    header = table[0]
    data_rows = table[1:]
    num_cols = len(header)
    
    # Check: all rows same width?
    for r_idx, row in enumerate(data_rows):
        if len(row) != num_cols:
            issues.append(f"COL_MISMATCH: row {r_idx+1} has {len(row)} cols, header has {num_cols}")
    
    # Check: empty header cells
    empty_headers = sum(1 for c in header if not c or not str(c).strip())
    if empty_headers > num_cols * 0.5:
        issues.append(f"EMPTY_HEADERS: {empty_headers}/{num_cols} header cells empty")
    
    # Check: all data rows empty?
    non_empty_rows = 0
    for row in data_rows:
        if any(c and str(c).strip() for c in row):
            non_empty_rows += 1
    if non_empty_rows == 0:
        issues.append("ALL_EMPTY: no data in any row")
    
    # Classify header
    classified, normed = classify_header(header)
    
    return issues, classified


def try_camelot_page(pdf_path, page_num):
    """Try camelot on a single page, return table count and issues."""
    try:
        import camelot
        tables = camelot.read_pdf(str(pdf_path), flavor="lattice",
                                 pages=str(page_num), strip_text="\n")
        if tables and len(tables) > 0:
            results = []
            for t in tables:
                if not t.df.empty and len(t.df) > 1:
                    results.append({
                        "rows": len(t.df) - 1,
                        "cols": len(t.df.columns),
                        "accuracy": t.accuracy,
                    })
            return results
        return []
    except Exception as e:
        return f"ERROR: {e}"


def audit_pdf(pdf_path):
    """Full audit of one PDF."""
    filename = os.path.basename(pdf_path)
    result = {
        "file": filename,
        "total_pages": 0,
        "has_text": False,
        "scope_pages": [],
        "tables_found": 0,
        "scope_tables": 0,
        "issues": [],
        "header_patterns": [],
        "camelot_pages": [],
        "sample_data": [],
    }
    
    with pdfplumber.open(pdf_path) as pdf:
        result["total_pages"] = len(pdf.pages)
        text_pages = 0
        
        for i, page in enumerate(pdf.pages):
            pg_num = i + 1
            text = page.extract_text() or ""
            if text.strip():
                text_pages += 1
            
            lower = text.lower()
            has_strong = any(kw in lower for kw in STRONG_KW)
            has_weak = any(kw in lower for kw in WEAK_KW)
            
            if not has_strong and not has_weak:
                continue
            
            # Extract tables from this page
            tables = page.extract_tables() or []
            
            page_info = {
                "page": pg_num,
                "strong_kw": has_strong,
                "tables": len(tables),
                "relevant_tables": 0,
                "issues": [],
            }
            
            for j, table in enumerate(tables):
                if not table or len(table) < 2:
                    continue
                
                result["tables_found"] += 1
                
                # Check if this table has relevant headers
                header_str = " ".join(str(c) for c in table[0] if c).lower()
                is_relevant = any(kw in header_str for kw in TABLE_HDR_KW)
                
                if is_relevant:
                    result["scope_tables"] += 1
                    page_info["relevant_tables"] += 1
                    
                    # Audit this table
                    issues, classified = audit_table(table, pg_num, j)
                    if issues:
                        for iss in issues:
                            page_info["issues"].append(f"T{j}: {iss}")
                    
                    # Record header pattern
                    normed_header = [normalize_header(c) for c in table[0]]
                    result["header_patterns"].append({
                        "page": pg_num,
                        "header": normed_header[:6],
                        "cols": len(table[0]),
                        "data_rows": len(table) - 1,
                        "classified": classified,
                    })
                    
                    # Sample first data row
                    if len(table) > 1:
                        sample = [str(c)[:40] if c else "" for c in table[1][:6]]
                        result["sample_data"].append({
                            "page": pg_num,
                            "row": sample,
                        })
            
            if has_strong or (has_weak and page_info["relevant_tables"] > 0):
                result["scope_pages"].append(page_info)
                if page_info["issues"]:
                    result["issues"].extend(
                        [f"pg{pg_num}: {iss}" for iss in page_info["issues"]]
                    )
        
        result["has_text"] = text_pages > 0
        
        # Try camelot on first 3 pages with tables
        table_page_nums = [p["page"] for p in result["scope_pages"]
                          if p["relevant_tables"] > 0][:3]
        for pg in table_page_nums:
            cam = try_camelot_page(pdf_path, pg)
            if isinstance(cam, str):
                result["camelot_pages"].append({"page": pg, "error": cam})
            elif cam:
                result["camelot_pages"].append({"page": pg, "tables": cam})
    
    return result


def main():
    all_pdfs = sorted(f for f in os.listdir(PDF_DIR) if f.endswith(".pdf"))
    print(f"Auditing {len(all_pdfs)} PDFs...\n")
    
    all_results = []
    
    for filename in all_pdfs:
        pdf_path = os.path.join(PDF_DIR, filename)
        result = audit_pdf(pdf_path)
        all_results.append(result)
        
        # Summary line
        n_scope = len(result["scope_pages"])
        n_tables = result["scope_tables"]
        n_issues = len(result["issues"])
        status = "OK" if n_issues == 0 else f"{n_issues} ISSUES"
        if not result["has_text"]:
            status = "SCANNED (no text)"
        elif n_tables == 0:
            status = "NO SCOPE TABLES"
        
        print(f"  {filename:45s} | {result['total_pages']:3d}pg | "
              f"{n_scope:2d} scope pg | {n_tables:2d} tables | {status}")
    
    # Write detailed report
    os.makedirs(os.path.dirname(REPORT_FILE), exist_ok=True)
    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        # === SUMMARY ===
        f.write("NCT PDF Deep Audit Report\n")
        f.write("=" * 80 + "\n\n")
        
        total = len(all_results)
        with_scope = sum(1 for r in all_results if r["scope_tables"] > 0)
        with_issues = sum(1 for r in all_results if r["issues"])
        scanned = sum(1 for r in all_results if not r["has_text"])
        total_tables = sum(r["scope_tables"] for r in all_results)
        
        f.write(f"Total PDFs:          {total}\n")
        f.write(f"With scope tables:   {with_scope}\n")
        f.write(f"With issues:         {with_issues}\n")
        f.write(f"Scanned-only:        {scanned}\n")
        f.write(f"Total scope tables:  {total_tables}\n\n")
        
        # === FILES WITH ISSUES ===
        f.write("=" * 80 + "\n")
        f.write("FILES WITH TABLE STRUCTURE ISSUES\n")
        f.write("=" * 80 + "\n\n")
        
        for r in all_results:
            if r["issues"]:
                f.write(f"--- {r['file']} ---\n")
                for iss in r["issues"]:
                    f.write(f"  {iss}\n")
                f.write("\n")
        
        if not any(r["issues"] for r in all_results):
            f.write("  None! All tables are structurally clean.\n\n")
        
        # === HEADER COLUMN COVERAGE ===
        f.write("=" * 80 + "\n")
        f.write("COLUMN COVERAGE PER FILE (which columns were detected)\n")
        f.write("=" * 80 + "\n\n")
        
        f.write(f"{'File':<45s} | {'sl':>3s} | {'scope':>5s} | {'cap':>3s} | {'cost':>4s} | {'time':>4s} | {'agcy':>4s}\n")
        f.write("-" * 80 + "\n")
        
        for r in all_results:
            if not r["header_patterns"]:
                f.write(f"{r['file']:<45s} | {'---':>3s} | {'---':>5s} | {'---':>3s} | {'---':>4s} | {'---':>4s} | {'---':>4s}\n")
                continue
            
            # Aggregate classified columns across all tables
            all_cols = set()
            for hp in r["header_patterns"]:
                all_cols.update(hp["classified"].keys())
            
            sl = "Y" if "sl" in all_cols else "-"
            scope = "Y" if "scope" in all_cols else "-"
            cap = "Y" if "capacity" in all_cols else "-"
            cost = "Y" if "cost" in all_cols else "-"
            time_ = "Y" if "timeline" in all_cols else "-"
            agcy = "Y" if "agency" in all_cols else "-"
            
            f.write(f"{r['file']:<45s} |  {sl:>2s} |   {scope:>2s} |  {cap:>2s} |   {cost:>2s} |   {time_:>2s} |   {agcy:>2s}\n")
        
        # === UNIQUE HEADER VARIANTS ===
        f.write("\n" + "=" * 80 + "\n")
        f.write("ALL UNIQUE SCOPE TABLE HEADER PATTERNS\n")
        f.write("=" * 80 + "\n\n")
        
        seen_patterns = set()
        for r in all_results:
            for hp in r["header_patterns"]:
                key = str(hp["header"][:4])
                if key not in seen_patterns:
                    seen_patterns.add(key)
                    f.write(f"  File: {r['file']} (pg {hp['page']}, {hp['data_rows']} rows, {hp['cols']} cols)\n")
                    f.write(f"  Header: {hp['header']}\n")
                    f.write(f"  Classified: {hp['classified']}\n\n")
        
        # === CAMELOT VS PDFPLUMBER ===
        f.write("=" * 80 + "\n")
        f.write("CAMELOT TABLE EXTRACTION STATUS\n")
        f.write("=" * 80 + "\n\n")
        
        for r in all_results:
            if r["camelot_pages"]:
                f.write(f"  {r['file']}:\n")
                for cp in r["camelot_pages"]:
                    if "error" in cp:
                        f.write(f"    pg{cp['page']}: {cp['error']}\n")
                    else:
                        for t in cp["tables"]:
                            f.write(f"    pg{cp['page']}: {t['rows']} rows, {t['cols']} cols, acc={t['accuracy']:.0f}%\n")
                f.write("\n")
        
        # === SAMPLE DATA ROWS ===
        f.write("=" * 80 + "\n")
        f.write("SAMPLE FIRST DATA ROW PER TABLE (verify data is real)\n")
        f.write("=" * 80 + "\n\n")
        
        for r in all_results:
            if r["sample_data"]:
                f.write(f"--- {r['file']} ---\n")
                for sd in r["sample_data"][:5]:
                    f.write(f"  pg{sd['page']}: {sd['row']}\n")
                if len(r["sample_data"]) > 5:
                    f.write(f"  ... +{len(r['sample_data'])-5} more\n")
                f.write("\n")
    
    print(f"\nDetailed report: {REPORT_FILE}")
    print(f"\nSummary: {with_scope}/{total} files have scope tables, "
          f"{with_issues} files with issues, {total_tables} total tables")


if __name__ == "__main__":
    main()
