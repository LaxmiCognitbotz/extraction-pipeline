"""
Pydantic-AI agent for CTUIL Revocation (24.6) PDF extraction.
Extracts the 14 core columns that are common across all 9 PDF vintages.

Page/table extraction is delegated to shared.pdf_table_extractor, which
provides annotated-markdown tables with column-path legends, multi-row
header detection, span carry-forward, and duplicate suppression.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import pdfplumber
from pydantic_ai import Agent

from shared.llm import get_model, ensure_api_key
from shared.pdf_table_extractor import build_page_bundle, build_page_bundles_from_pdf
from app.revocation_extraction.models import PageExtractionResult, RevocationRecord

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """
You are a data extraction agent for CTUIL Revocation (Regulation 24.6) PDF reports.
Extract every row from the table. The output schema fields and their descriptions
are your complete specification — follow them exactly.

Structural Context:
- Each page bundle starts with "--- PAGE N of M ---" so you always know which page you are on.
- "=== PAGE TEXT ===" contains the full human-readable text — PRIMARY source of truth.
- "=== TABLE N (annotated markdown) ===" contains the structured table with a [COLUMN PATHS]
  legend that maps every column index to its full header path (e.g. Col 3: Application ID).
  Use the legend to align values to fields; never rely on visual tab-position alone.

Behavioral Rules:
1. Read BOTH "=== PAGE TEXT ===" and "=== TABLE ===" for every page.
   PAGE TEXT is primary — use it to recover values missing in the table.
2. Never skip a row, even if some columns are empty.
3. Only blank cells, dashes "-", cells with only spaces, or "⏎" intra-cell markers should be null.
4. Do NOT include the serial number (Sl. No. / Sr. No.) in any field.
5. Do NOT invent, guess, or estimate values. Extract only what is literally printed.
6. Never copy values from one row into another.

Column-Type Guard Rules (CRITICAL — prevent mis-mapped columns):
7. 'Present Connectivity / deemed GNA (MW)' MUST be a numeric MW value (e.g. "250").
   NEVER a date. If you see a date here, the row has shifted — re-align from PAGE TEXT.
8. 'SCOD as per Application' MUST be a date (e.g. "31-Mar-25") or null. Never "0".
9. 'Updated / Revised SCOD' and '24.6 Compliance Due Date' must be dates, "NA", "No", or null —
   never numeric strings like "0" or status strings like "Not commissioned".
10. 'Connectivity Status' must be a status text (e.g. "Effective"). NEVER a date. Re-align if so.
11. '24.6 Compliance Due Date' must be a date string. NEVER a status text like "Commissioned".

Row-shift Recovery:
- When a shift is detected, fall back to PAGE TEXT and re-map all columns by semantic meaning,
  not by column position.
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

    with pdfplumber.open(str(pdf_path)) as pdf:
        total = len(pdf.pages)
        logger.info("[%s]  %d page(s)", filename, total)

        # Detect upto_month from the first page text
        first_text = pdf.pages[0].extract_text(x_tolerance=3, y_tolerance=3) or ""
        detected_upto = _extract_upto_month(first_text)
        if detected_upto:
            logger.info("[%s]  upto_month: %s", filename, detected_upto)

        # Build page bundles using the shared extractor (produces annotated markdown)
        page_bundles: list[str] = []
        for start in range(0, total, pages_per_chunk):
            chunk_parts: list[str] = []
            for i, page in enumerate(pdf.pages[start: start + pages_per_chunk], start + 1):
                chunk_parts.append(build_page_bundle(page, i, total))
            page_bundles.append("\n".join(chunk_parts))

    logger.info("[%s]  %d chunk(s) → LLM", filename, len(page_bundles))

    for i, bundle in enumerate(page_bundles, 1):
        logger.info("[%s]  chunk %d/%d", filename, i, len(page_bundles))
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
                            single = build_page_bundle(pdf.pages[pg_idx], pg_idx + 1, total)
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
