from pathlib import Path
from app.converter import convert_pdf_to_markdown
from app.config import settings

def main():
    pdf_path = Path("uploads/CTUIL-Transmission-Reports/2026/03_March/TBCB_UC_Report.pdf")
    output_dir = Path("scratch/output")
    output_dir.mkdir(parents=True, exist_ok=True)
    
    md_path = convert_pdf_to_markdown(pdf_path, output_dir)
    print(f"Markdown saved to: {md_path}")
    print("\nFirst 1000 characters of Markdown:")
    print(md_path.read_text(encoding='utf-8')[:1000])

if __name__ == "__main__":
    main()
