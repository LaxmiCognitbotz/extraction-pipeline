import pdfplumber
import os

pdf_path = r"d:\Projects\extraction-pipeline\uploads\CEA-NCT-Minutes\01_40th_NCT_MoM.pdf"

with pdfplumber.open(pdf_path) as pdf:
    for i, page in enumerate(pdf.pages):
        text = page.extract_text()
        if text and "Scope of" in text and "Transmission" in text:
            print(f"--- Page {i+1} ---")
            lines = text.split('\n')
            for idx, line in enumerate(lines):
                if "Scope of" in line and "Transmission" in line:
                    print(f"Found heading: {line}")
                    # Print context
                    start = max(0, idx - 5)
                    end = min(len(lines), idx + 20)
                    for l in lines[start:end]:
                        print(l)
            
            tables = page.extract_tables()
            for j, table in enumerate(tables):
                print(f"Table {j}:")
                for row in table[:5]: # Print first 5 rows
                    print(row)
