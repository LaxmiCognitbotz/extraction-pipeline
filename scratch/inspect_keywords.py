"""Quick inspect: dump pages that have scope keywords from one PDF."""
import pdfplumber, sys

pdf_path = sys.argv[1] if len(sys.argv) > 1 else r"d:\Projects\extraction-pipeline\uploads\CEA-NCT-Minutes\01_40th_NCT_MoM.pdf"

# These are the EXACT keywords we look for on each page
KEYWORDS = [
    "scope of the transmission",
    "scope of works",
    "scope of work",
    "scope of the scheme",
    "estimated cost",
    "implementation timeline",
    "implementation time-frame",
    "implementation timeframe",
    "capacity /km",
    "capacity/km",
    "capacity (mva)",
]

with pdfplumber.open(pdf_path) as pdf:
    for i, page in enumerate(pdf.pages):
        text = page.extract_text() or ""
        lower = text.lower()
        
        found = [kw for kw in KEYWORDS if kw in lower]
        if found:
            tables = page.extract_tables() or []
            real_tables = [t for t in tables if t and len(t) > 1]
            print(f"\n{'='*60}")
            print(f"PAGE {i+1} - Keywords: {found}")
            print(f"Tables: {len(real_tables)}")
            print(f"{'='*60}")
            print(text[:500])
            if real_tables:
                print(f"\n--- Table headers ---")
                for j, t in enumerate(real_tables):
                    print(f"  T{j}: {[str(c)[:30] if c else '' for c in t[0]]}")
                    print(f"      {len(t)-1} data rows")
