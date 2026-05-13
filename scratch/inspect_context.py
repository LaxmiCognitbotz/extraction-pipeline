import pdfplumber
import os

pdf_path = r"d:\Projects\extraction-pipeline\uploads\CEA-NCT-Minutes\01_40th_NCT_MoM.pdf"

with open(r"d:\Projects\extraction-pipeline\scratch\context_output.txt", "w", encoding="utf-8") as out:
    with pdfplumber.open(pdf_path) as pdf:
        for i in range(5, 15): # Pages 6 to 15
            if i >= len(pdf.pages): break
            page = pdf.pages[i]
            text = page.extract_text()
            out.write(f"--- Page {i+1} ---\n{text}\n")
