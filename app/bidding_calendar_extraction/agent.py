"""
Pydantic-AI agent for CTUIL Bidding Calendar PDF extraction.

Provider : shared.llm.get_model()
Schema   : output_type=PageExtractionResult (Pydantic injects full field contract)
Prompt   : minimal — generic extraction rules only
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import pdfplumber
from pydantic_ai import Agent

from shared.llm import get_model, ensure_api_key
from app.bidding_calendar_extraction.models import (
    BiddingSchemeRecord,
    PageExtractionResult,
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# System prompt — minimal.
# All field-level instructions are in Field(description=...) in models.py,
# which Pydantic-AI injects automatically via output_type.
# ─────────────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """
You are a data extraction agent for CTUIL Bidding Calendar PDF reports.
Extract every transmission scheme row. The output schema (injected separately)
is the complete specification — read each field's description carefully.

Extraction rules (not repeated in the schema):
1. Never skip a row, even if it looks incomplete.
2. Blank / dash cell → null.
3. Carry the region header forward — every row belongs to the last seen region.
4. The scheme title and its bullet sub-items are in the SAME table cell —
   the first line is transmission_scheme; all bullet lines go in major_elements.
5. Multi-line status text → join all bullet lines with ' | ' into one string.
""".strip()


# ─────────────────────────────────────────────────────────────────────────────
# Date from filename helper
# ─────────────────────────────────────────────────────────────────────────────

_FILENAME_DATE_RE = re.compile(r"(\d{2})-(\d{2})-(\d{4})")


def _date_from_filename(filename: str) -> str | None:
    """Extract DD-MM-YYYY from filename like '01_Bidding Calendar 31-03-2026.pdf'."""
    m = _FILENAME_DATE_RE.search(filename)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return None


# ─────────────────────────────────────────────────────────────────────────────
# PDF page → structured text bundle
# ─────────────────────────────────────────────────────────────────────────────

def _extract_page_bundle(page: Any) -> str:
    """
    Build a text bundle for the LLM from one pdfplumber page.
    Two sections per page:
      === PAGE TEXT ===          raw text (for region headers, titles, dates)
      === TABLE N (cells) ===    tab-separated cell matrix (for data columns)
    """
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
        lines.append(f"=== TABLE {idx + 1} (cells, tab-separated) ===")
        for row in tbl:
            cleaned: list[str] = []
            for cell in row:
                if cell is None:
                    cleaned.append("")
                else:
                    s = str(cell).strip()
                    s = re.sub(r"[\r\n]+", " | ", s)   # preserve bullet structure
                    s = re.sub(r"[ \t]{2,}", " ", s)
                    cleaned.append(s)
            lines.append("\t".join(cleaned))
        lines.append("")

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Agent (lazy singleton)
# ─────────────────────────────────────────────────────────────────────────────

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


# ─────────────────────────────────────────────────────────────────────────────
# Main public function
# ─────────────────────────────────────────────────────────────────────────────

def extract_pdf_with_agent(
    pdf_path: Path,
    pages_per_chunk: int = 4,
) -> list[BiddingSchemeRecord]:
    """
    Extract all bidding scheme records from a single Bidding Calendar PDF.

    Returns a flat list of BiddingSchemeRecord, with source_file and
    bidding_calendar_date injected on every record after the agent call.
    """
    agent = _get_agent()
    filename = pdf_path.name
    filename_date = _date_from_filename(filename)
    detected_date: str | None = None
    all_records: list[BiddingSchemeRecord] = []

    with pdfplumber.open(str(pdf_path)) as pdf:
        total = len(pdf.pages)
        logger.info("[%s]  %d page(s)", filename, total)

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
                f"Extract all bidding scheme records:\n\n{bundle}"
            ).output

            if result.bidding_calendar_date and not detected_date:
                detected_date = result.bidding_calendar_date
            all_records.extend(result.records)

        except Exception as exc:
            err_str = str(exc)
            # Pydantic-AI loop detection fires when a large chunk has many
            # similar-looking rows. Retry by splitting to 1 page at a time.
            if "looping content" in err_str or "loop detection" in err_str.lower():
                logger.warning(
                    "[%s] chunk %d: loop detection triggered — retrying 1 page at a time",
                    filename, i,
                )
                # Re-build sub-bundles for each individual page in this chunk
                start_page = (i - 1) * pages_per_chunk
                end_page   = min(start_page + pages_per_chunk, total)
                for pg_idx in range(start_page, end_page):
                    try:
                        with pdfplumber.open(str(pdf_path)) as pdf:
                            single_bundle = (
                                f"--- PAGE {pg_idx + 1} of {total} ---\n"
                                + _extract_page_bundle(pdf.pages[pg_idx])
                            )
                        sub_result: PageExtractionResult = agent.run_sync(
                            f"Extract all bidding scheme records:\n\n{single_bundle}"
                        ).output
                        if sub_result.bidding_calendar_date and not detected_date:
                            detected_date = sub_result.bidding_calendar_date
                        all_records.extend(sub_result.records)
                    except Exception as sub_exc:  # noqa: BLE001
                        logger.error(
                            "[%s] page %d retry failed: %s",
                            filename, pg_idx + 1, sub_exc, exc_info=True,
                        )
            else:
                logger.error("[%s] chunk %d failed: %s", filename, i, exc, exc_info=True)

    # Resolve final calendar date: PDF-detected > filename-derived
    calendar_date = detected_date or filename_date

    # Inject source_file and bidding_calendar_date on every record
    final: list[BiddingSchemeRecord] = []
    for rec in all_records:
        d = rec.model_dump()
        d["source_file"] = filename
        d["bidding_calendar_date"] = calendar_date
        final.append(BiddingSchemeRecord(**d))

    logger.info("[%s]  %d record(s) extracted", filename, len(final))
    return final

