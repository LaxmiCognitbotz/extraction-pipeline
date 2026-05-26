"""
Pydantic-AI agent for CTUIL Revocation (24.6) PDF extraction.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import pdfplumber
from pydantic_ai import Agent

from shared.llm import get_model, ensure_api_key
from app.revocation_extraction.models import PageExtractionResult, RevocationRecord

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """
You are a data extraction agent for CTUIL Revocation (Regulation 24.6) PDF reports.
Extract every row from the table. The output schema is the complete specification.

Extraction rules:
1. Never skip a row, even if incomplete.
2. Blank / dash / "-" cell → null.
3. Multi-line header → join words with a single space for the key in row_data.
4. Do NOT include the serial number (Sl. No. / Sr. No.) in row_data or anywhere.

Application ID parsing (CRITICAL):
  The Application ID cell may contain BOTH the connectivity ID AND an LTA ID.

  Pattern A — older PDFs, Application ID cell is clean:
    Cell: "1200003331"  → application_id="1200003331"
    LTA info appears below the name in the Applicant Name cell:
      "Gujarat State Electricity Corporation Limited
       LTA: 1200003326 (100MW) & 1200003327 (500MW)"
    → applicant_name="Gujarat State Electricity Corporation Limited"
    → lta_id="1200003326 (100MW) & 1200003327 (500MW)"

  Pattern B — newer PDFs, Application ID cell contains multiple IDs:
    Cell: "St-II: 312100010 (45 MW) (5 out of 45 MW), 312100012 (60 MW) LTA: 0412100011(65 MW)"
    → application_id="312100010, 312100012"
    → lta_id="0412100011 (65 MW)"

  If no LTA ID is present → lta_id=null.
  Always strip LTA/Connectivity lines from applicant_name.

row_data keys: use the EXACT column header text (multi-line joined with space).
Do not include: Sl. No., Application ID, Applicant Name columns in row_data.
""".strip()


# ── Upto-month from PDF title ─────────────────────────────────────────────────
_UPTO_RE = re.compile(
    r"(?:upto|Final\s+list)\s+([A-Za-z]+['\u2019\u2018]?\d{2})",
    re.IGNORECASE,
)


def _extract_upto_month(text: str) -> str | None:
    m = _UPTO_RE.search(text)
    if m:
        return m.group(1).replace("\u2019", "'").replace("\u2018", "'")
    return None


# ── Page → text bundle ────────────────────────────────────────────────────────

def _extract_page_bundle(page: Any) -> str:
    lines: list[str] = []

    raw_text = page.extract_text(x_tolerance=3, y_tolerance=3) or ""
    lines.append("=== PAGE TEXT ===")
    lines.append(raw_text.strip())
    lines.append("")

    tables = page.extract_tables(
        table_settings={
            "vertical_strategy": "lines",
            "horizontal_strategy": "lines",
            "snap_tolerance": 5,
            "join_tolerance": 5,
        }
    )
    if not tables:
        tables = page.extract_tables(
            table_settings={
                "vertical_strategy": "text",
                "horizontal_strategy": "lines",
                "snap_tolerance": 5,
                "join_tolerance": 3,
            }
        )

    for idx, tbl in enumerate(tables):
        lines.append(f"=== TABLE {idx + 1} (tab-separated) ===")
        for row in tbl:
            cleaned = []
            for cell in row:
                if cell is None:
                    cleaned.append("")
                else:
                    s = str(cell).strip()
                    s = re.sub(r"[\r\n]+", " | ", s)
                    s = re.sub(r"[ \t]{2,}", " ", s)
                    cleaned.append(s)
            lines.append("\t".join(cleaned))
        lines.append("")

    return "\n".join(lines)


# ── Agent singleton ───────────────────────────────────────────────────────────

_agent: Agent | None = None


def _get_agent() -> Agent:
    global _agent
    if _agent is None:
        ensure_api_key()
        model = get_model()
        _agent = Agent(
            model=model,
            output_type=PageExtractionResult,
            system_prompt=SYSTEM_PROMPT,
        )
        logger.info("Agent ready — model: %s", model)
    return _agent


# ── Main extraction function ──────────────────────────────────────────────────

def extract_pdf_with_agent(
    pdf_path: Path,
    pages_per_chunk: int = 3,
) -> list[RevocationRecord]:
    agent = _get_agent()
    filename = pdf_path.name
    detected_upto: str | None = None
    all_records: list[RevocationRecord] = []
    total = 0

    with pdfplumber.open(str(pdf_path)) as pdf:
        total = len(pdf.pages)
        logger.info("[%s]  %d page(s)", filename, total)

        # Try to get upto_month from first page text directly
        first_text = pdf.pages[0].extract_text(x_tolerance=3, y_tolerance=3) or ""
        detected_upto = _extract_upto_month(first_text)
        if detected_upto:
            logger.info("[%s]  upto_month detected from title: %s", filename, detected_upto)

        bundles: list[str] = []
        for start in range(0, total, pages_per_chunk):
            parts: list[str] = []
            for i, page in enumerate(pdf.pages[start: start + pages_per_chunk], start + 1):
                parts.append(f"--- PAGE {i} of {total} ---")
                parts.append(_extract_page_bundle(page))
            bundles.append("\n".join(parts))

    logger.info("[%s]  %d chunk(s) → LLM", filename, len(bundles))

    for i, bundle in enumerate(bundles, 1):
        logger.info("[%s]  chunk %d/%d", filename, i, len(bundles))
        try:
            result: PageExtractionResult = agent.run_sync(
                f"Extract all revocation records:\n\n{bundle}"
            ).output

            if result.upto_month and not detected_upto:
                detected_upto = result.upto_month

            all_records.extend(result.records)

        except Exception as exc:
            err_str = str(exc)
            if "looping content" in err_str or "loop detection" in err_str.lower():
                logger.warning(
                    "[%s] chunk %d: loop detection — retrying 1 page at a time",
                    filename, i,
                )
                start_page = (i - 1) * pages_per_chunk
                end_page = min(start_page + pages_per_chunk, total)
                for pg_idx in range(start_page, end_page):
                    try:
                        with pdfplumber.open(str(pdf_path)) as pdf:
                            single = (
                                f"--- PAGE {pg_idx + 1} of {total} ---\n"
                                + _extract_page_bundle(pdf.pages[pg_idx])
                            )
                        sub: PageExtractionResult = agent.run_sync(
                            f"Extract all revocation records:\n\n{single}"
                        ).output
                        if sub.upto_month and not detected_upto:
                            detected_upto = sub.upto_month
                        all_records.extend(sub.records)
                    except Exception as sub_exc:
                        logger.error(
                            "[%s] page %d retry failed: %s",
                            filename, pg_idx + 1, sub_exc, exc_info=True,
                        )
            else:
                logger.error("[%s] chunk %d failed: %s", filename, i, exc, exc_info=True)

    # Inject source_file and upto_month on every record
    final: list[RevocationRecord] = []
    for rec in all_records:
        d = rec.model_dump()
        d["source_file"] = filename
        d["upto_month"] = detected_upto
        final.append(RevocationRecord(**d))

    logger.info("[%s]  %d record(s)", filename, len(final))
    return final
