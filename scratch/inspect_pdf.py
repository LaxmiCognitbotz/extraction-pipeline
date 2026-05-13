import pdfplumber
import os

pdf_path = r"d:\Projects\extraction-pipeline\uploads\CEA-NCT-Minutes\03_38th_NCT_MoM.pdf"

with pdfplumber.open(pdf_path) as pdf:
    print(f"Total pages: {len(pdf.pages)}")
    found = False
    for i, page in enumerate(pdf.pages):
        text = page.extract_text()
        if not text:
            print(f"Page {i+1}: No text extracted")
            continue
        
        # print(f"Page {i+1} text (first 100 chars): {text[:100]}")
        
        if "Scope of Transmission Scheme" in text:
            print(f"--- Found 'Scope of Transmission Scheme' on Page {i+1} ---")
            print(text)
            tables = page.extract_tables()
            for j, table in enumerate(tables):
                print(f"Table {j}:")
                for row in table:
                    print(row)
            found = True
            break
    if not found:
        print("Search term not found in this PDF.")
