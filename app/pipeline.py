"""End-to-end pipeline orchestrator.

PDF → Camelot → Smart Chunks → PydanticAI → Post-Process → JSON.
Supports single-file and batch processing.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from app.config import settings
from app.converter import extract_tables_from_pdf
from app.extractor import extract_from_corpus
from app.schemas import DocType, ExtractionResult


# ── Doc-type inference ─────────────────────────────────────────────────


def _infer_doc_type(pdf_name: str) -> DocType:
    """Infer the document type from the PDF filename."""
    name_lower = pdf_name.lower()

    if "rtm" in name_lower:
        return DocType.RTM_UC_REPORT
    if "nct" in name_lower:
        return DocType.NCT_REPORT
    if re.search(r"commis|completed|\bcomm\b|_comm|^comm|comm_|-comm|comm-", name_lower):
        return DocType.TBCB_COMM_REPORT
    if re.search(r"\buc\b|_uc|^uc|uc_|-uc|uc-", name_lower):
        return DocType.TBCB_UC_REPORT

    raise ValueError(
        f"Cannot infer doc_type from '{pdf_name}'. "
        f"Valid: {[d.value for d in DocType]}"
    )


def _build_output_subdir(pdf_path: Path) -> Path:
    """Build output subdir mirroring uploads folder structure."""
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
    """Run the full extraction pipeline on a single PDF."""
    pdf_path = Path(pdf_path).resolve()

    out_dir = Path(output_dir) if output_dir else _build_output_subdir(pdf_path)
    out_dir.mkdir(parents=True, exist_ok=True)

    if doc_type is None:
        doc_type = _infer_doc_type(pdf_path.name)
    elif isinstance(doc_type, str):
        doc_type = DocType(doc_type)

    print(f"\n{'='*60}")
    print(f"  Extraction Pipeline (Camelot + PydanticAI)")
    print(f"  PDF:      {pdf_path.name}")
    print(f"  Doc Type: {doc_type.value}")
    print(f"  Region:   {region or '(auto)'}")
    print(f"  Output:   {out_dir}")
    print(f"{'='*60}\n")

    if skip_conversion:
        md_path = out_dir / f"{pdf_path.stem}.md"
        if not md_path.exists():
            raise FileNotFoundError(f"Markdown not found at {md_path}")
        print(f"[pipeline] Using existing {md_path.name}")
        from app.extractor import extract_elements
        result = extract_elements(md_path, doc_type, region)
        result.source_pdf = str(pdf_path)
    else:
        print("[pipeline] Step 1/3: Camelot table extraction")
        corpus = extract_tables_from_pdf(pdf_path, out_dir)

        if not corpus.chunks:
            print("[pipeline] [WARN] No tables found!")
            result = ExtractionResult(
                doc_type=doc_type, region=region,
                source_pdf=str(pdf_path),
            )
        else:
            print(f"\n[pipeline] Step 2/3: PydanticAI extraction ({len(corpus.chunks)} chunks)")
            result = extract_from_corpus(
                corpus=corpus, doc_type=doc_type,
                region=region, source_pdf=str(pdf_path),
            )

    # Step 3: Write output
    print("\n[pipeline] Step 3/3: Writing output")
    output_path = out_dir / f"{pdf_path.stem}_extracted.json"

    output_data = {
        "metadata": {
            "doc_type": result.doc_type.value,
            "region": result.region,
            "source_pdf": result.source_pdf,
            "element_count": result.element_count,
            "extracted_at": datetime.now(timezone.utc).isoformat(),
            "model": settings.model_name,
        },
        "elements": [
            elem.model_dump(mode="json", by_alias=True) for elem in result.elements
        ],
    }

    output_path.write_text(
        json.dumps(output_data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"[pipeline] [OK] JSON saved -> {output_path}")

    # Generate Master Excel
    if output_data["elements"]:
        excels_dir = settings.project_root / "excels"
        excels_dir.mkdir(parents=True, exist_ok=True)
        master_excel = excels_dir / "Element Status.xlsx"
        
        new_df = pd.DataFrame(output_data["elements"])
        
        if master_excel.exists():
            try:
                df_existing = pd.read_excel(master_excel, header=None, skiprows=2)
                df_existing.dropna(how="all", inplace=True)
                combined_data = df_existing.values.tolist() + new_df.values.tolist()
                df_combined = pd.DataFrame(combined_data)
            except Exception as e:
                print(f"[pipeline] [WARN] Failed to read existing master Excel: {e}. Starting fresh.")
                df_combined = new_df
        else:
            df_combined = new_df
        
        # Build exact grouped headers requested by user
        multi_cols = [
            ("Element Code", ""),
            ("Inter/Intra Tx. Element", ""),
            ("Transmission Scheme", ""),
            ("Transmission Scope", ""),
            ("MVA", ""),
            ("Status", ""),
            ("Approval of Elements in which NCT", ""),
            ("Source", ""),
            ("Tender Issuing Authority", ""),
            ("Date of tender issuance", ""),
            ("Date of Bid Submission", ""),
            ("Execution Timeline", ""),
            ("Tentative SCOD", ""),
            ("Awarded To", ""),
            ("Project Cost (Cr.) (NCT)", ""),
            ("SPV Transfer Date", ""),
            ("Physical Progress S/s of Tx. Line", "Length"),
            ("Physical Progress S/s of Tx. Line", "Location"),
            ("Physical Progress S/s of Tx. Line", "Foundation"),
            ("Physical Progress S/s of Tx. Line", "Erection"),
            ("Physical Progress S/s of Tx. Line", "Stringing"),
            ("Physical Progress S/s of Tx. Line", "Foundation (%)"),
            ("Physical Progress S/s of Tx. Line", "Erection (%)"),
            ("Physical Progress S/s of Tx. Line", "Stringing (%)"),
            ("Physical Progress Substation", "Civil Work (%)"),
            ("Physical Progress Substation", "Equipment Received (%)"),
            ("Physical Progress Substation", "Equipment Erected (%)"),
            ("Original SCOD", ""),
            ("Anticipated SCOD", ""),
            ("Remarks", ""),
        ]
        
        df_combined.columns = pd.MultiIndex.from_tuples(multi_cols)
        
        with pd.ExcelWriter(master_excel, engine="openpyxl") as writer:
            df_combined.to_excel(writer, sheet_name="Element Status", index=True)
            # Delete the first column (the pandas index)
            worksheet = writer.sheets["Element Status"]
            worksheet.delete_cols(1)
            
        print(f"[pipeline] [OK] Master Excel updated -> {master_excel}")

    return result


# ── Batch Processing ───────────────────────────────────────────────────


def discover_pdfs(uploads_root: str | Path | None = None) -> list[Path]:
    """Recursively find all PDFs under the uploads directory."""
    root = Path(uploads_root) if uploads_root else settings.uploads_dir
    return sorted(root.rglob("*.pdf"))


def run_batch(
    uploads_root: str | Path | None = None,
    region: str = "",
    skip_conversion: bool = False,
    dry_run: bool = False,
) -> list[dict]:
    """Run the pipeline on ALL PDFs in the uploads tree."""
    pdfs = discover_pdfs(uploads_root)

    print(f"\n{'#'*60}")
    print(f"  BATCH: {len(pdfs)} PDFs")
    print(f"{'#'*60}\n")

    summary: list[dict] = []

    for i, pdf in enumerate(pdfs, 1):
        rel_path = pdf.relative_to(
            Path(uploads_root or settings.uploads_dir).resolve()
        )

        try:
            inferred = _infer_doc_type(pdf.name)
        except ValueError:
            inferred = None

        entry = {
            "index": i, "file": str(rel_path), "pdf_name": pdf.name,
            "doc_type": inferred.value if inferred else "UNKNOWN",
            "status": "pending", "element_count": 0, "error": None,
        }

        if dry_run:
            entry["status"] = "dry_run"
            summary.append(entry)
            print(f"  [{i:02d}/{len(pdfs)}] {rel_path} -> {entry['doc_type']}")
            continue

        if not inferred:
            entry["status"] = "skipped"
            entry["error"] = "Unknown doc_type"
            summary.append(entry)
            continue

        try:
            result = run_pipeline(
                pdf, inferred, region, skip_conversion=skip_conversion
            )
            entry["status"] = "success"
            entry["element_count"] = result.element_count
        except Exception as e:
            entry["status"] = "error"
            entry["error"] = str(e)
            print(f"  [{i:02d}/{len(pdfs)}] [ERR] {pdf.name}: {e}")

        summary.append(entry)

    ok = sum(1 for s in summary if s["status"] == "success")
    err = sum(1 for s in summary if s["status"] == "error")
    total_el = sum(s["element_count"] for s in summary)
    print(f"\n{'='*60}")
    print(f"  Done: {ok} ok, {err} errors, {total_el} total elements")
    print(f"{'='*60}\n")

    return summary
