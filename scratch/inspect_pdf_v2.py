import pdfplumber

pdf_path = r"d:\Projects\extraction-pipeline\uploads\CEA-NCT-Minutes\01_40th_NCT_MoM.pdf"

with pdfplumber.open(pdf_path) as pdf:
    for i, page in enumerate(pdf.pages):
        text = page.extract_text()
        if text and "Scope" in text:
            print(f"--- Page {i+1} ---")
            # print(text)
            tables = page.extract_tables()
            for j, table in enumerate(tables):
                # Look for tables that might be transmission schemes
                if table and any("Scope" in str(cell) for row in table for cell in row if cell):
                    print(f"Table {j} on page {i+1} seems relevant:")
                    for row in table:
                        print(row)
