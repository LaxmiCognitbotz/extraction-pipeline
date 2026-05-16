from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path

import pdfplumber


PDF_DIR = Path("uploads/CEA-NCT-Minutes")
OUT_JSON = Path("scratch/nct_variant_audit.json")
OUT_MD = Path("scratch/nct_variant_audit.md")


KEYWORDS = {
    "scope": [
        "scope of the transmission",
        "scope of transmission",
        "scope of works",
        "scope of work",
        "scope of the scheme",
        "scope of the transmission scheme",
    ],
    "summary": [
        "name of the scheme and tentative",
        "estimated cost",
        "mode of implementation",
        "recommended under",
        "approved under",
        "bpc",
    ],
    "modification": [
        "modification",
        "revised",
        "earlier approved",
        "earlier recommended",
    ],
    "decision": [
        "nct approved",
        "nct recommended",
        "after detailed deliberations",
        "nct decided",
    ],
}


def clean(value: object) -> str:
    if value is None:
        return ""
    text = str(value).replace("\uf0b7", "-")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def table_text(table: list[list[object]]) -> str:
    return " ".join(clean(cell) for row in table for cell in row).lower()


def first_rows(table: list[list[object]], n: int = 3) -> str:
    return " | ".join(" ; ".join(clean(c) for c in row) for row in table[:n]).lower()


def classify_table(table: list[list[object]]) -> str:
    txt = table_text(table)
    head = first_rows(table, 4)
    cols = max((len(r) for r in table if r), default=0)

    if not txt:
        return "empty"
    if "scope of works" in txt or "scope of work" in txt:
        return "scope_works_text_or_table"
    if "scope of the transmission scheme" in txt or "scope of transmission scheme" in txt:
        if "original" in head and "revised" in head:
            return "modification_original_vs_revised_scope_table"
        if "estimated cost" in head or "cost" in head:
            return "scope_table_with_cost"
        if "capacity" in head:
            return "scope_table_with_capacity"
        return "scope_table"
    if "name of the scheme and tentative" in txt and "estimated" in txt and "remarks" in txt:
        return "scheme_summary_cost_remarks_table"
    if "name of scheme" in txt and "estimated cost" in txt and "bpc" in txt:
        return "scheme_summary_cost_bpc_table"
    if "transmission scheme" in txt and any(k in txt for k in ["recommended", "approved", "noted"]):
        return "status_register_table"
    if "implementation timeline" in txt or "implementation timeframe" in txt:
        return "implementation_timeline_table"
    if "estimated cost" in txt or "cost estimate" in txt:
        return "cost_table"
    if cols <= 2 and any(k in txt for k in ["note:", "quantity", "amc includes"]):
        return "continuation_note_table"
    return "other_table"


def text_signals(text: str) -> list[str]:
    lower = text.lower()
    signals: list[str] = []
    for name, kws in KEYWORDS.items():
        if any(kw in lower for kw in kws):
            signals.append(name)
    return signals


def meeting_from_name(name: str) -> str:
    m = re.search(r"(\d+)(?:st|nd|rd|th)?", name, re.IGNORECASE)
    return m.group(1) if m else ""


def audit_pdf(path: Path) -> dict:
    result = {
        "file": path.name,
        "meeting_number": meeting_from_name(path.name),
        "pages": 0,
        "text_pages": 0,
        "scanned_or_no_text_pages": 0,
        "table_count": 0,
        "relevant_pages": [],
        "table_variants": Counter(),
        "examples": defaultdict(list),
    }

    with pdfplumber.open(str(path)) as pdf:
        result["pages"] = len(pdf.pages)
        for page_no, page in enumerate(pdf.pages, 1):
            text = page.extract_text() or ""
            if text.strip():
                result["text_pages"] += 1
            else:
                result["scanned_or_no_text_pages"] += 1

            signals = text_signals(text)
            tables = page.extract_tables() or []
            page_variants: list[str] = []

            for table_idx, table in enumerate(tables):
                if not table or len(table) < 1:
                    continue
                variant = classify_table(table)
                result["table_count"] += 1
                result["table_variants"][variant] += 1
                page_variants.append(variant)
                if len(result["examples"][variant]) < 3:
                    result["examples"][variant].append({
                        "page": page_no,
                        "table": table_idx,
                        "header_sample": first_rows(table, 2)[:500],
                    })

            if signals or any(v != "other_table" for v in page_variants):
                result["relevant_pages"].append({
                    "page": page_no,
                    "signals": signals,
                    "table_variants": sorted(set(page_variants)),
                })

    result["table_variants"] = dict(result["table_variants"])
    result["examples"] = dict(result["examples"])
    return result


def high_level_family(item: dict) -> str:
    variants = set(item["table_variants"])
    if "scheme_summary_cost_remarks_table" in variants:
        return "modern_summary_plus_scope"
    if "modification_original_vs_revised_scope_table" in variants:
        return "modification_original_vs_revised"
    if "scope_table_with_cost" in variants:
        return "scope_tables_with_cost_columns"
    if "scope_table_with_capacity" in variants or "scope_table" in variants:
        return "scope_tables_capacity_only"
    if "scheme_summary_cost_bpc_table" in variants:
        return "early_summary_cost_bpc"
    if item["scanned_or_no_text_pages"] > item["pages"] * 0.4:
        return "partly_scanned_or_ocr_needed"
    return "paragraph_or_status_only"


def main() -> None:
    pdfs = sorted(PDF_DIR.glob("*.pdf"))
    results = [audit_pdf(path) for path in pdfs]
    for item in results:
        item["family"] = high_level_family(item)

    OUT_JSON.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")

    family_counts = Counter(item["family"] for item in results)
    variant_counts = Counter()
    for item in results:
        variant_counts.update(item["table_variants"])

    lines = [
        "# NCT PDF Variant Audit",
        "",
        f"Scanned PDFs: {len(results)}",
        "",
        "## High-Level Families",
        "",
    ]
    for family, count in family_counts.most_common():
        lines.append(f"- {family}: {count}")

    lines.extend(["", "## Table Variants", ""])
    for variant, count in variant_counts.most_common():
        lines.append(f"- {variant}: {count}")

    lines.extend(["", "## Per-PDF Classification", ""])
    lines.append("| File | Pages | No-text pages | Family | Relevant pages | Main table variants |")
    lines.append("| --- | ---: | ---: | --- | --- | --- |")
    for item in results:
        rel_pages = ", ".join(str(p["page"]) for p in item["relevant_pages"][:20])
        if len(item["relevant_pages"]) > 20:
            rel_pages += f", ... +{len(item['relevant_pages']) - 20}"
        variants = ", ".join(f"{k}({v})" for k, v in sorted(item["table_variants"].items()))
        lines.append(
            f"| {item['file']} | {item['pages']} | {item['scanned_or_no_text_pages']} | "
            f"{item['family']} | {rel_pages} | {variants} |"
        )

    lines.extend(["", "## Extraction Strategy By Family", ""])
    lines.extend([
        "- modern_summary_plus_scope: pair the summary table row with the nearest following detailed scope table; preserve continuation pages.",
        "- modification_original_vs_revised: extract only revised scope/cost/timeline where present, while keeping original/revised columns separate.",
        "- scope_tables_with_cost_columns: extract directly row-by-row; cost is often row-level or scheme-level inside the same table.",
        "- scope_tables_capacity_only: extract scope/capacity rows, then inherit cost/timeline/BPC from surrounding paragraphs or summary/status tables.",
        "- early_summary_cost_bpc: summary table supplies scheme/cost/BPC; detailed scope may be later and must be joined by heading.",
        "- paragraph_or_status_only: use paragraph decisions and status tables as context; do not invent missing scope rows.",
        "- partly_scanned_or_ocr_needed: pdfplumber text is incomplete; needs OCR fallback before reliable extraction.",
    ])

    OUT_MD.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {OUT_JSON}")
    print(f"Wrote {OUT_MD}")
    print("Families:", dict(family_counts))


if __name__ == "__main__":
    main()
