"""Post-processing and mapping from extracted NCT elements to report rows.

This module is intentionally deterministic (no LLM calls). It:
- Computes MVA from scope text (multiplier logic).
- Normalizes execution timeline to numeric months.
- Optionally enriches tender-related dates/awarded-to from locally downloaded tender PDFs.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import date
import calendar
from pathlib import Path
from typing import Iterable, Optional

import pdfplumber

from nct_extraction.schemas import NCTElement, NCTReport, NCTReportRow
from nct_extraction.tender_query import suggest_queries
from nct_extraction.extraction.tender_tools import download_pfccl_tender_pdfs, download_recpdcl_tender_pdfs


_DATE_PATTERNS = [
    # 31.03.2029 / 31-03-2029 / 31/03/2029
    r"\b(?P<d>\d{1,2})[./-](?P<m>\d{1,2})[./-](?P<y>\d{4})\b",
    # 31 Mar 2029 / 31 March 2029
    r"\b(?P<d>\d{1,2})\s+(?P<mon>Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec|January|February|March|April|June|July|August|September|October|November|December)\s+(?P<y>\d{4})\b",
]


def _parse_date_any(text: str) -> list[date]:
    found: list[date] = []

    for pat in _DATE_PATTERNS:
        for m in re.finditer(pat, text, flags=re.IGNORECASE):
            try:
                if "mon" in m.groupdict() and m.group("mon"):
                    mon_txt = m.group("mon").lower()
                    month_map = {
                        "jan": 1,
                        "january": 1,
                        "feb": 2,
                        "february": 2,
                        "mar": 3,
                        "march": 3,
                        "apr": 4,
                        "april": 4,
                        "may": 5,
                        "jun": 6,
                        "june": 6,
                        "jul": 7,
                        "july": 7,
                        "aug": 8,
                        "august": 8,
                        "sep": 9,
                        "sept": 9,
                        "september": 9,
                        "oct": 10,
                        "october": 10,
                        "nov": 11,
                        "november": 11,
                        "dec": 12,
                        "december": 12,
                    }
                    month = month_map.get(mon_txt[:4], month_map.get(mon_txt[:3]))
                    if not month:
                        continue
                    found.append(date(int(m.group("y")), int(month), int(m.group("d"))))
                else:
                    found.append(date(int(m.group("y")), int(m.group("m")), int(m.group("d"))))
            except Exception:
                continue

    # de-dupe while preserving order
    out: list[date] = []
    seen: set[date] = set()
    for d in found:
        if d not in seen:
            seen.add(d)
            out.append(d)
    return out


def parse_execution_timeline_months(text: str) -> Optional[int]:
    if not text:
        return None
    m = re.search(r"\b(\d{1,3})\s*(?:months?|mos?)\b", text, flags=re.IGNORECASE)
    if m:
        try:
            return int(m.group(1))
        except Exception:
            return None
    # Some tables use only a number
    m2 = re.search(r"\b(\d{1,3})\b", text)
    if m2:
        try:
            return int(m2.group(1))
        except Exception:
            return None
    return None


def parse_mva_total(scope_text: str) -> Optional[int]:
    """Compute transformer capacity totals.

    Examples:
      - 3x1500MVA => 4500
      - 6x1500MVA + 5x500MVA => 11500

    Heuristic: sums all occurrences of <n> x <mva> MVA in the text.
    """
    if not scope_text:
        return None

    total = 0
    matched = False

    # Normalize common variants: 3 X 1500, 3×1500, 3x1500
    norm = scope_text.replace("×", "x")
    for m in re.finditer(r"\b(\d{1,2})\s*x\s*(\d{2,5})\s*MVA\b", norm, flags=re.IGNORECASE):
        matched = True
        try:
            total += int(m.group(1)) * int(m.group(2))
        except Exception:
            continue

    if matched:
        return total

    # If already a single capacity like "1500 MVA"
    single = re.search(r"\b(\d{2,5})\s*MVA\b", norm, flags=re.IGNORECASE)
    if single:
        try:
            return int(single.group(1))
        except Exception:
            return None

    return None


def add_months(d: date, months: int) -> date:
    """Add months to a date, clamping to month-end when needed."""
    year = d.year + (d.month - 1 + months) // 12
    month = (d.month - 1 + months) % 12 + 1
    day = d.day

    # Clamp day to last day of target month
    last_day = calendar.monthrange(year, month)[1]
    day = min(day, last_day)
    return date(year, month, day)


@dataclass(frozen=True)
class TenderEvidence:
    rfp_pdf: Optional[Path]
    amendment_pdfs: tuple[Path, ...]
    result_pdfs: tuple[Path, ...]
    other_pdfs: tuple[Path, ...] = tuple()


def find_tender_pdfs(download_root: Path, query: str, *, preferred_dir: Path | None = None) -> TenderEvidence:
    """Best-effort lookup of tender PDFs under local downloads.

    Expected structure (if using the bundled scraper):
      uploads/PFCCL-INDIA-TENDER/<folder>/*.pdf
      uploads/RECPDCL-RECTPCL-TENDER/<folder>/*.pdf

    This function never downloads anything; it only searches locally.
    """
    if not query:
        return TenderEvidence(None, tuple(), tuple())

    tokens = [t for t in re.split(r"[^A-Za-z0-9]+", query.lower()) if len(t) >= 4]
    if not tokens:
        return TenderEvidence(None, tuple(), tuple())

    tender_dirs: list[Path] = []
    if preferred_dir and preferred_dir.exists():
        tender_dirs.append(preferred_dir)
    tender_dirs.extend(
        [
            download_root / "PFCCL-INDIA-TENDER",
            download_root / "RECPDCL-RECTPCL-TENDER",
        ]
    )

    candidate_pdfs: list[Path] = []
    for tdir in tender_dirs:
        if not tdir.exists():
            continue
        candidate_pdfs.extend([p for p in tdir.rglob("*.pdf") if p.is_file()])

    if not candidate_pdfs:
        return TenderEvidence(None, tuple(), tuple())

    def score_pdf(p: Path) -> int:
        name = p.name.lower()
        return sum(1 for tok in tokens if tok in name)

    ranked = sorted(candidate_pdfs, key=score_pdf, reverse=True)
    top = [p for p in ranked[:250] if score_pdf(p) > 0]

    rfp = None
    amendments: list[Path] = []
    results: list[Path] = []
    others: list[Path] = []

    for p in top:
        n = p.name.lower()
        if rfp is None and "rfp" in n:
            rfp = p
        if any(k in n for k in ["amendment", "corrigendum", "extension", "postponement"]):
            amendments.append(p)
        if any(k in n for k in ["successful", "result", "qualified", "award"]):
            results.append(p)
        if p not in amendments and p not in results and p != rfp:
            others.append(p)

    return TenderEvidence(rfp, tuple(amendments), tuple(results), tuple(others))


def _extract_first_page_text(pdf_path: Path) -> str:
    try:
        with pdfplumber.open(str(pdf_path)) as pdf:
            if not pdf.pages:
                return ""
            return (pdf.pages[0].extract_text() or "").strip()
    except Exception:
        return ""


def _extract_all_text_limited(pdf_path: Path, max_pages: int = 3) -> str:
    """Extract text from up to max_pages for date/bidder heuristics."""
    try:
        with pdfplumber.open(str(pdf_path)) as pdf:
            texts: list[str] = []
            for i, page in enumerate(pdf.pages[:max_pages]):
                texts.append(page.extract_text() or "")
            return "\n".join(texts)
    except Exception:
        return ""


def extract_rfp_date(rfp_pdf: Path) -> Optional[date]:
    text = _extract_first_page_text(rfp_pdf)
    if not text:
        return None

    # Prefer dates near "Date" / "Dated"
    lines = text.splitlines()
    near = "\n".join([ln for ln in lines if re.search(r"\b(date|dated)\b", ln, re.IGNORECASE)])
    candidates = _parse_date_any(near) or _parse_date_any(text)
    return candidates[0] if candidates else None


def extract_latest_bid_submission_date(amendment_pdfs: Iterable[Path]) -> Optional[date]:
    best: Optional[date] = None
    for pdf in amendment_pdfs:
        text = _extract_all_text_limited(pdf, max_pages=3)
        if not text:
            continue

        # Prefer lines mentioning bid submission / last date / due date
        lines = text.splitlines()
        focus = "\n".join(
            ln for ln in lines
            if re.search(r"\b(bid|submission|due\s*date|last\s*date)\b", ln, re.IGNORECASE)
        )
        dates = _parse_date_any(focus) or _parse_date_any(text)
        for d in dates:
            if best is None or d > best:
                best = d
    return best


def extract_awarded_to(result_pdfs: Iterable[Path]) -> str:
    """Best-effort extraction of successful bidder/ranked bidder."""
    for pdf in result_pdfs:
        text = _extract_all_text_limited(pdf, max_pages=2)
        if not text:
            continue
        # Simple heuristics
        m = re.search(
            r"\b(?:successful\s+bidder|awarded\s+to|ranked\s+bidder)\b\s*[:\-]?\s*(.+)",
            text,
            flags=re.IGNORECASE,
        )
        if m:
            val = m.group(1).strip()
            val = re.split(r"[\r\n]{1,2}", val)[0].strip()
            return val[:200]
    return ""


def extract_spv_transfer_date(pdfs: Iterable[Path]) -> Optional[date]:
    """Best-effort extraction of SPV transfer date from any tender-related PDF."""
    for pdf in pdfs:
        text = _extract_all_text_limited(pdf, max_pages=3)
        if not text:
            continue
        lines = text.splitlines()
        focus = "\n".join(
            ln for ln in lines
            if re.search(r"\bspv\b", ln, re.IGNORECASE) and re.search(r"\btransfer\b", ln, re.IGNORECASE)
        )
        dates = _parse_date_any(focus) or _parse_date_any(text)
        if dates:
            return dates[0]
    return None


def element_to_report_row(
    elem: NCTElement,
    meeting_name: str,
    *,
    tender_download_root: Path,
    preferred_tender_dir: Path | None = None,
) -> NCTReportRow:
    mva = None
    if elem.capacity_mva is not None:
        try:
            mva = int(round(float(elem.capacity_mva)))
        except Exception:
            mva = None
    if mva is None:
        mva = parse_mva_total(elem.scope)

    months = parse_execution_timeline_months(elem.execution_timeline)

    cost_text = (elem.project_cost_text or "").strip()
    if not cost_text and elem.project_cost_cr is not None:
        cost_text = str(elem.project_cost_cr)

    evidence = find_tender_pdfs(
        tender_download_root,
        elem.scope or elem.scheme_name,
        preferred_dir=preferred_tender_dir,
    )
    tender_issue = extract_rfp_date(evidence.rfp_pdf) if evidence.rfp_pdf else None
    bid_sub = extract_latest_bid_submission_date(evidence.amendment_pdfs) if evidence.amendment_pdfs else None
    awarded_to = extract_awarded_to(evidence.result_pdfs) if evidence.result_pdfs else ""
    spv_transfer = extract_spv_transfer_date(
        [p for p in [evidence.rfp_pdf] if p] + list(evidence.amendment_pdfs) + list(evidence.result_pdfs) + list(evidence.other_pdfs)
    )

    tentative_scod = None
    if bid_sub and months:
        tentative_scod = add_months(bid_sub, months)

    return NCTReportRow(
        transmission_scheme=(elem.scheme_name or "").strip(),
        transmission_scope=(elem.scope or "").strip(),
        mva=mva,
        approval_of_elements_in_which_nct=meeting_name,
        tender_issuing_authority=(elem.tender_issuing_authority or "").strip(),
        date_of_tender_issuance=tender_issue,
        date_of_bid_submission=bid_sub,
        execution_timeline_months=months,
        tentative_scod=tentative_scod,
        awarded_to=awarded_to,
        project_cost_cr=cost_text,
        spv_transfer_date=spv_transfer,
    )


def build_report(
    meeting_name: str,
    source_pdf: str,
    elements: list[NCTElement],
    *,
    tender_download_root: Path | None = None,
) -> NCTReport:
    download_root = tender_download_root or (Path(__file__).parent.parent / "uploads")
    # Permanent default: ON. Disable by setting DISABLE_AUTO_DOWNLOAD_TENDERS=true.
    disable_auto = os.getenv("DISABLE_AUTO_DOWNLOAD_TENDERS", "false").strip().lower() in {"1", "true", "yes", "y"}
    auto_download = not disable_auto
    max_downloads = int(os.getenv("AUTO_DOWNLOAD_TENDERS_MAX", "3") or "3")

    preferred_dirs: dict[tuple[str, str], Path] = {}

    if auto_download and max_downloads > 0:
        attempted: set[tuple[str, str]] = set()
        used = 0
        for e in elements:
            if used >= max_downloads:
                break

            authority = (e.tender_issuing_authority or "").strip().lower()
            if not authority:
                continue

            bucket = ""
            if any(x in authority for x in ["pfc", "pfccl"]):
                bucket = "pfccl"
            elif any(x in authority for x in ["rec", "recpdcl", "rectpcl"]):
                bucket = "recpdcl"
            else:
                continue

            scheme_key = (e.scheme_name or "").strip() or (e.scope or "").strip()[:80]
            key = (bucket, scheme_key)
            if key in attempted:
                continue
            attempted.add(key)

            existing = find_tender_pdfs(download_root, (e.scope or e.scheme_name or ""))
            if existing.rfp_pdf or existing.amendment_pdfs or existing.result_pdfs:
                continue

            queries = suggest_queries(e.scheme_name or "", e.scope or "", limit=6)
            if not queries:
                continue

            for q in queries[:2]:
                try:
                    if bucket == "pfccl":
                        pref = download_pfccl_tender_pdfs(query=q)
                    else:
                        pref = download_recpdcl_tender_pdfs(query=q)
                    preferred_dirs[key] = pref
                    used += 1
                    break
                except Exception:
                    continue

    rows: list[NCTReportRow] = []
    for e in elements:
        authority = (e.tender_issuing_authority or "").strip().lower()
        bucket = ""
        if any(x in authority for x in ["pfc", "pfccl"]):
            bucket = "pfccl"
        elif any(x in authority for x in ["rec", "recpdcl", "rectpcl"]):
            bucket = "recpdcl"
        scheme_key = (e.scheme_name or "").strip() or (e.scope or "").strip()[:80]
        pref_dir = preferred_dirs.get((bucket, scheme_key))
        rows.append(
            element_to_report_row(
                e,
                meeting_name,
                tender_download_root=download_root,
                preferred_tender_dir=pref_dir,
            )
        )
    return NCTReport(meeting_name=meeting_name, source_pdf=source_pdf, rows=rows)
