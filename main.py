"""CLI entry point for the Element Status Sheet Extraction Pipeline.

Usage:
    # -- Single PDF (full pipeline: PDF -> Markdown -> JSON) --
    uv run python main.py uploads/CTUIL-Transmission-Reports/2026/03_March/RTM_UC_Report.pdf

    # With explicit doc_type and region:
    uv run python main.py uploads/.../TBCB_UC_Report.pdf --doc-type TBCB_UC_Report --region "Northern Region"

    # Skip PDF conversion (Markdown already exists in output/):
    uv run python main.py uploads/.../RTM_UC_Report.pdf --skip-conversion

    # -- Extraction only (from existing Markdown) --
    uv run python main.py --extract-only output/RTM_UC_Report.md --doc-type RTM_UC_Report

    # -- Batch: dry-run to see all PDFs & inferred types --
    uv run python main.py --batch --dry-run

    # -- Batch: process ALL PDFs --
    uv run python main.py --batch
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app.extractor import extract_elements
from app.pipeline import run_batch, run_pipeline
from app.schemas import DocType


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="extraction-pipeline",
        description="Extract structured element data from CTUIL transmission PDFs.",
    )

    parser.add_argument(
        "pdf_path",
        nargs="?",
        help="Path to the source PDF file (single-file mode).",
    )
    parser.add_argument(
        "--doc-type",
        choices=[d.value for d in DocType],
        default=None,
        help="Document type (auto-inferred from filename if omitted).",
    )
    parser.add_argument(
        "--region",
        default="",
        help="Region name for contextual disambiguation.",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Override output directory (default: output/<year>/<month>/).",
    )
    parser.add_argument(
        "--skip-conversion",
        action="store_true",
        help="Skip PDF->Markdown conversion (assume .md already exists).",
    )
    parser.add_argument(
        "--extract-only",
        default=None,
        help="Run extraction only on an existing Markdown file (skip PDF step).",
    )

    # Batch mode
    parser.add_argument(
        "--batch",
        action="store_true",
        help="Process ALL PDFs found under the uploads directory.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="With --batch: only list PDFs and inferred doc types, no processing.",
    )
    parser.add_argument(
        "--uploads-root",
        default=None,
        help="Override uploads root directory for --batch mode.",
    )

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    # Batch mode
    if args.batch:
        run_batch(
            uploads_root=args.uploads_root,
            region=args.region,
            skip_conversion=args.skip_conversion,
            dry_run=args.dry_run,
        )
        return

    # Extract-only mode
    if args.extract_only:
        if not args.doc_type:
            print("Error: --doc-type is required with --extract-only")
            sys.exit(1)

        md_path = Path(args.extract_only)
        if not md_path.exists():
            print(f"Error: Markdown file not found: {md_path}")
            sys.exit(1)

        print(f"[main] Extract-only mode: {md_path.name}")
        result = extract_elements(md_path, args.doc_type, args.region)

        # Write output
        import json
        from datetime import datetime, timezone

        from app.config import settings

        out_dir = Path(args.output_dir) if args.output_dir else settings.output_dir
        out_dir.mkdir(parents=True, exist_ok=True)
        output_path = out_dir / f"{md_path.stem}_extracted.json"

        output_data = {
            "metadata": {
                "doc_type": result.doc_type.value,
                "region": result.region,
                "source_markdown": str(md_path),
                "element_count": result.element_count,
                "extracted_at": datetime.now(timezone.utc).isoformat(),
                "model": settings.model_name,
                "framework": "pydantic-ai",
            },
            "elements": [
                elem.model_dump(mode="json") for elem in result.elements
            ],
        }

        output_path.write_text(
            json.dumps(output_data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"[main] Saved {result.element_count} elements -> {output_path}")
        return

    # Full pipeline mode
    if not args.pdf_path:
        parser.print_help()
        sys.exit(1)

    pdf_path = Path(args.pdf_path)
    if not pdf_path.exists():
        print(f"Error: PDF file not found: {pdf_path}")
        sys.exit(1)

    run_pipeline(
        pdf_path=pdf_path,
        doc_type=args.doc_type,
        region=args.region,
        output_dir=args.output_dir,
        skip_conversion=args.skip_conversion,
    )


if __name__ == "__main__":
    main()
