import pdfplumber
import os

pdf_dir = r"d:\Projects\extraction-pipeline\uploads\CEA-NCT-Minutes"
files = ["01_40th_NCT_MoM.pdf", "05_36th_NCT_MoM.pdf", "10_31st_NCT_MoM.pdf"]

output_file = r"d:\Projects\extraction-pipeline\scratch\pdf_headers.txt"

with open(output_file, "w", encoding="utf-8") as out:
    for filename in files:
        path = os.path.join(pdf_dir, filename)
        out.write(f"\n--- File: {filename} ---\n")
        try:
            with pdfplumber.open(path) as pdf:
                text = pdf.pages[0].extract_text()
                out.write(text[:1000] if text else "No text on page 0")
                out.write("\n")
        except Exception as e:
            out.write(f"Error reading {filename}: {e}\n")
