"""PDF -> Markdown converter using opendataloader-pdf.

Wraps the opendataloader_pdf.convert() call to produce Markdown files
from source PDFs, placing output in the configured output directory.
"""

from pathlib import Path

import opendataloader_pdf

from app.config import settings


def convert_pdf_to_markdown(
    pdf_path: str | Path,
    output_dir: str | Path | None = None,
) -> Path:
    """Convert a single PDF to Markdown using opendataloader-pdf.

    Args:
        pdf_path: Absolute or relative path to the input PDF.
        output_dir: Directory to write the .md output into.
                    Defaults to ``settings.output_dir``.

    Returns:
        Path to the generated Markdown file.

    Raises:
        FileNotFoundError: If the source PDF does not exist.
        RuntimeError: If conversion produces no output.
    """
    pdf_path = Path(pdf_path).resolve()
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    out_dir = Path(output_dir) if output_dir else settings.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"[converter] Converting {pdf_path.name} -> Markdown ...")

    opendataloader_pdf.convert(
        input_path=[str(pdf_path)],
        output_dir=str(out_dir),
        format=settings.pdf_output_format,
    )

    md_path = out_dir / f"{pdf_path.stem}.md"
    if not md_path.exists():
        raise RuntimeError(
            f"Conversion completed but output not found at {md_path}"
        )

    print(f"[converter] [OK] Written {md_path}  ({md_path.stat().st_size:,} bytes)")
    return md_path
