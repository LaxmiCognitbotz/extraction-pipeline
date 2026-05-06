"""End-to-end pipeline orchestrator.

Chains: PDF -> Markdown (converter) -> JSON (extractor) -> validated output.
Supports single-file and batch processing across the uploads tree.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from app.config import settings
from app.converter import convert_pdf_to_markdown
from app.extractor import extract_elements
from app.schemas import DocType, ExtractionResult


# ── Doc-type inference ─────────────────────────────────────────────────


def _infer_doc_type(pdf_name: str) -> DocType:
    """Infer the document type from the PDF filename.

    Handles varied naming conventions across months:
        RTM_UC_Report.pdf, RTM_UC.pdf, UC_RTM_Report.pdf
        TBCB_Comm_Report.pdf, Comm_TBCB_Report.pdf, TBCB_Comm.pdf
        TBCB_UC_Report.pdf, UC_TBCB_Report.pdf, TBCB_UC.pdf

    Priority order (checked first wins):
        1. RTM keywords  -> RTM_UC_Report
        2. NCT keywords  -> NCT_Report
        3. Commissioned / Completed / Comm keywords -> TBCB_Comm_Report
        4. UC / Under-Construction keywords -> TBCB_UC_Report

    Raises:
        ValueError: If no pattern matches.
    """
    name_lower = pdf_name.lower()

    # 1. RTM (always has "rtm" in the name)
    if "rtm" in name_lower:
        return DocType.RTM_UC_REPORT

    # 2. NCT
    if "nct" in name_lower:
        return DocType.NCT_REPORT

    # 3. Commissioned / Completed
    if re.search(
        r"commission|completed|_comm[_.\s]|^comm_|report_comm|_comm$|comm_tbcb|tbcb_comm",
        name_lower,
    ):
        return DocType.TBCB_COMM_REPORT

    # 4. Under Construction
    if re.search(
        r"_uc[_.\s]|^uc_|_uc$|report_uc|tbcb.*uc|uc.*tbcb|tbcb_uc|uc_tbcb",
        name_lower,
    ):
        return DocType.TBCB_UC_REPORT

    raise ValueError(
        f"Cannot infer doc_type from filename '{pdf_name}'. "
        f"Please specify doc_type explicitly. "
        f"Valid values: {[d.value for d in DocType]}"
    )


def _build_output_subdir(pdf_path: Path) -> Path:
    """Build an output subdirectory mirroring the uploads folder structure.

    e.g. uploads/CTUIL-.../2025/03_March/Report_UC.pdf
         -> output/2025/03_March/
    """
    parts = pdf_path.parts
    for i, part in enumerate(parts):
        if re.match(r"^20\d{2}$", part) and i + 1 < len(parts):
            return settings.output_dir / part / parts[i + 1]
    return settings.output_dir


def run_pipeline(
    pdf_path: str | Path,
    doc_type: str | DocType | None = None,
    region: str = "",
    output_dir: str | Path | None = None,
    skip_conversion: bool = False,
) -> ExtractionResult:
    """Run the full extraction pipeline on a single PDF.

    Steps:
        1. Convert PDF -> Markdown (or skip if already converted)
        2. Send Markdown + system prompt to Gemini via Pydantic AI Agent
        3. Receive validated Pydantic objects (structured output)
        4. Write output to ``output/<year>/<month>/<stem>_extracted.json``

    Args:
        pdf_path: Path to the source PDF file.
        doc_type: Document type. Auto-inferred from filename if None.
        region: Optional region name for contextual disambiguation.
        output_dir: Override for the output directory.
        skip_conversion: If True, skip PDF->MD conversion.

    Returns:
        Validated ``ExtractionResult`` with all extracted elements.
    """
    pdf_path = Path(pdf_path).resolve()

    if output_dir:
        out_dir = Path(output_dir)
    else:
        out_dir = _build_output_subdir(pdf_path)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Infer doc_type if not provided
    if doc_type is None:
        doc_type = _infer_doc_type(pdf_path.name)
    elif isinstance(doc_type, str):
        doc_type = DocType(doc_type)

    print(f"\n{'='*60}")
    print(f"  Element Status Sheet Extraction Pipeline (Pydantic AI)")
    print(f"  PDF:      {pdf_path.name}")
    print(f"  Doc Type: {doc_type.value}")
    print(f"  Region:   {region or '(not specified)'}")
    print(f"  Output:   {out_dir}")
    print(f"{'='*60}\n")

    # Step 1: Convert PDF -> Markdown
    if skip_conversion:
        md_path = out_dir / f"{pdf_path.stem}.md"
        if not md_path.exists():
            raise FileNotFoundError(
                f"skip_conversion=True but Markdown not found at {md_path}"
            )
        print(f"[pipeline] Skipping conversion -- using existing {md_path.name}")
    else:
        print("[pipeline] Step 1/3: Converting PDF -> Markdown")
        md_path = convert_pdf_to_markdown(pdf_path, out_dir)

    # Step 2: Extract via Pydantic AI Agent
    print("\n[pipeline] Step 2/3: Extracting elements via Pydantic AI Agent")
    result = extract_elements(md_path, doc_type, region)
    result.source_pdf = str(pdf_path)

    # Step 3: Write output JSON
    print("\n[pipeline] Step 3/3: Writing validated output")
    output_json_path = out_dir / f"{pdf_path.stem}_extracted.json"

    output_data = {
        "metadata": {
            "doc_type": result.doc_type.value,
            "region": result.region,
            "source_pdf": result.source_pdf,
            "source_markdown": result.source_markdown,
            "element_count": result.element_count,
            "extracted_at": datetime.now(timezone.utc).isoformat(),
            "model": settings.model_name,
            "framework": "pydantic-ai",
        },
        "elements": [
            elem.model_dump(mode="json") for elem in result.elements
        ],
    }

    output_json_path.write_text(
        json.dumps(output_data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print(f"[pipeline] [OK] Saved {result.element_count} elements -> {output_json_path}")
    print(f"[pipeline] [OK] Pipeline complete!\n")

    return result


# ── Batch Processing ───────────────────────────────────────────────────


def discover_pdfs(uploads_root: str | Path | None = None) -> list[Path]:
    """Recursively find all PDFs under the uploads directory."""
    root = Path(uploads_root) if uploads_root else settings.uploads_dir
    pdfs = sorted(root.rglob("*.pdf"))
    return pdfs


def run_batch(
    uploads_root: str | Path | None = None,
    region: str = "",
    skip_conversion: bool = False,
    dry_run: bool = False,
) -> list[dict]:
    """Run the extraction pipeline on ALL PDFs in the uploads tree.

    Args:
        uploads_root: Root directory containing PDF files.
        region: Optional region for all documents.
        skip_conversion: Skip PDF->MD for all files.
        dry_run: If True, only list what would be processed.

    Returns:
        List of dicts with per-file results summary.
    """
    pdfs = discover_pdfs(uploads_root)

    print(f"\n{'#'*60}")
    print(f"  BATCH PROCESSING: {len(pdfs)} PDFs found")
    print(f"{'#'*60}\n")

    summary: list[dict] = []

    for i, pdf in enumerate(pdfs, 1):
        rel_path = pdf.relative_to(
            Path(uploads_root or settings.uploads_dir).resolve()
        )

        try:
            inferred_type = _infer_doc_type(pdf.name)
        except ValueError:
            inferred_type = None

        entry = {
            "index": i,
            "file": str(rel_path),
            "pdf_name": pdf.name,
            "inferred_doc_type": (
                inferred_type.value if inferred_type else "UNKNOWN"
            ),
            "status": "pending",
            "element_count": 0,
            "error": None,
        }

        if dry_run:
            entry["status"] = "dry_run"
            summary.append(entry)
            print(
                f"  [{i:02d}/{len(pdfs)}] {rel_path}  ->  "
                f"{entry['inferred_doc_type']}"
            )
            continue

        if inferred_type is None:
            entry["status"] = "skipped"
            entry["error"] = "Could not infer doc_type"
            summary.append(entry)
            print(
                f"  [{i:02d}/{len(pdfs)}] [WARN] SKIPPED {rel_path} "
                f" (unknown type)"
            )
            continue

        try:
            result = run_pipeline(
                pdf_path=pdf,
                doc_type=inferred_type,
                region=region,
                skip_conversion=skip_conversion,
            )
            entry["status"] = "success"
            entry["element_count"] = result.element_count
        except Exception as e:
            entry["status"] = "error"
            entry["error"] = str(e)
            print(f"  [{i:02d}/{len(pdfs)}] [ERR] ERROR on {pdf.name}: {e}")

        summary.append(entry)

    # Print summary
    success = sum(1 for s in summary if s["status"] == "success")
    errors = sum(1 for s in summary if s["status"] == "error")
    skipped = sum(1 for s in summary if s["status"] == "skipped")
    total_elements = sum(s["element_count"] for s in summary)

    print(f"\n{'='*60}")
    print(f"  BATCH SUMMARY")
    print(f"  Total PDFs:     {len(pdfs)}")
    print(f"  Successful:     {success}")
    print(f"  Errors:         {errors}")
    print(f"  Skipped:        {skipped}")
    print(f"  Total elements: {total_elements}")
    print(f"{'='*60}\n")

    return summary
