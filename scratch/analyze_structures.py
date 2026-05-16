import pdfplumber
import os

pdf_dir = r"d:\Projects\extraction-pipeline\uploads\CEA-NCT-Minutes"
all_files = [f for f in os.listdir(pdf_dir) if f.endswith(".pdf")]
all_files.sort()

# Sample a variety of files: first 3, middle 3, last 3
sample_indices = [0, 1, 2, 10, 20, 30, 40, 45, 47]
samples = [all_files[i] for i in sample_indices if i < len(all_files)]

output_file = r"d:\Projects\extraction-pipeline\scratch\all_structures.txt"

with open(output_file, "w", encoding="utf-8") as out:
    for filename in samples:
        path = os.path.join(pdf_dir, filename)
        out.write(f"\n{'='*20} File: {filename} {'='*20}\n")
        try:
            with pdfplumber.open(path) as pdf:
                found_tables = 0
                for i, page in enumerate(pdf.pages):
                    text = page.extract_text()
                    if not text:
                        out.write(f"Page {i+1}: No text extracted (possibly scanned)\n")
                        continue
                    
                    if any(keyword in text for keyword in ["Scope", "Transmission", "Scheme", "Annexure"]):
                        tables = page.extract_tables()
                        for j, table in enumerate(tables):
                            if table and len(table) > 1:
                                out.write(f"Page {i+1} Table {j} Headers: {table[0]}\n")
                                found_tables += 1
                    if found_tables > 5: break # Limit per file
                if found_tables == 0:
                    out.write("No relevant tables found in first pages.\n")
        except Exception as e:
            out.write(f"Error reading {filename}: {e}\n")
