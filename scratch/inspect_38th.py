import pdfplumber

pdf_path = r"d:\Projects\extraction-pipeline\uploads\CEA-NCT-Minutes\03_38th_NCT_MoM.pdf"

with open(r"d:\Projects\extraction-pipeline\scratch\inspect_38th_all.txt", "w", encoding="utf-8") as out:
    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages):
            tables = page.extract_tables()
            if tables:
                out.write(f"--- Page {i+1} has {len(tables)} tables ---\n")
                # text = page.extract_text()
                # out.write(text[:200] + "...\n")
                for j, table in enumerate(tables):
                    if table and len(table) > 1:
                        out.write(f"Table {j} headers: {table[0]}\n")
            if i > 50: break
