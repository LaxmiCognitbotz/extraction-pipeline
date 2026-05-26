"""
Pydantic-AI agent module for CTUIL Renewable Energy Margin PDF extraction.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import pdfplumber
from pydantic_ai import Agent

from shared.llm import get_model, ensure_api_key
from app.re_margin_extraction.models import (
    NonRESubstationMarginResult,
    ProposedRESubstationMarginResult,
    RESubstationMarginResult,
)

logger = logging.getLogger(__name__)

# =─────────────────────────────────────────────────────────────────────────────
# Prompts
# =─────────────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT_NON_RE = """
You are a data extraction agent for CTUIL Non-RE Substations Margin PDF reports.
Extract every substation row by reading BOTH the "=== PAGE TEXT ===" and the "=== TABLE ===" sections.

Rules:
1. The "=== PAGE TEXT ===" is your primary source of truth and contains the full text lines for each row. Use it to recover values if the "=== TABLE ===" has empty, missing, or null cells.
2. Carry the State header forward. For example, if a row contains only a state name like "Gujarat" or "Maharashtra", remember it and set it as the `state` field for every substation row that follows, until a new state header appears.
3. Never skip any substation rows.
4. If a cell or text line contains '0' (zero), you must extract it exactly as '0'. Do NOT convert '0' to null. Only blank cells, dashes, or cells containing only spaces should be returned as null.
5. Extract the station name, capacity, allocations, margins, and bays required exactly as printed.

Critical Anti-Hallucination Rules:
- ONLY extract values that are explicitly present in the provided text. Do NOT invent, guess, or estimate any value.
- If a value is genuinely not present or cannot be found in either the page text or table, return null. Never fill in a number that is not printed.
- Do NOT copy values from one row to another incorrectly. Each substation row is independent.
- Never extrapolate or infer numeric values. Extract only what is literally printed.
""".strip()

SYSTEM_PROMPT_PROPOSED_RE = """
You are a data extraction agent for CTUIL Proposed RE Substations Margin PDF reports (older reports).
These reports are typically titled: "Status of margins available at existing ISTS substations (non RE) for proposed RE integration".
Extract every substation row by reading BOTH the "=== PAGE TEXT ===" and the "=== TABLE ===" sections.

Rules:
1. The "=== PAGE TEXT ===" is your primary source of truth and contains the full text lines for each row. Use it to recover values if the "=== TABLE ===" has empty, missing, or null cells.
2. Carry the State header forward. If a row contains only a state name (e.g., 'Gujarat', 'Maharashtra'), remember it and set it as the `state` field for all subsequent substation rows, until a new state header appears.
3. Never skip any substation rows.
4. If a cell or text line contains '0' (zero), you must extract it exactly as '0'. Do NOT convert '0' to null. Only blank cells, dashes, or cells containing only spaces should be returned as null.
5. Carefully extract the Existing, Under Implementation, and Planned transformation capacities (both 765/400kV and 400/220kV levels) from their respective columns.

Critical Anti-Hallucination Rules:
- ONLY extract values that are explicitly present in the provided text. Do NOT invent, guess, or estimate any value.
- If a value is genuinely not present or cannot be found in either the page text or table, return null. Never fill in a number that is not printed.
- Do NOT copy values from one row to another incorrectly. Each substation row is independent.
- Never extrapolate or infer numeric values. Extract only what is literally printed.
""".strip()

SYSTEM_PROMPT_RE = """
You are a data extraction agent for CTUIL RE Substations Margin PDF reports.
Extract every pooling station row by reading BOTH the "=== PAGE TEXT ===" and the "=== TABLE ===" sections.

Rules:
1. The "=== PAGE TEXT ===" is your primary source of truth and contains the full text lines for each row. Use it to recover values if the "=== TABLE ===" has empty, missing, or null cells.
2. Carry the Region header forward. If a row starts with a region like 'Northern Region', 'Western Region', etc., remember it and set it as the `region` field for all subsequent pooling station rows until a new region appears.
3. Carry the Category header forward. If a row contains section headers like 'A. Existing RE Pooling Stations', 'B. Under Implementation RE Pooling Stations', etc., remember it and set it as the `category` field for all subsequent rows until a new category header appears.
4. Never skip any pooling station rows.
5. If a cell or text line contains '0' (zero), you must extract it exactly as '0'. Do NOT convert '0' to null. Only blank cells, dashes, or cells containing only spaces should be returned as null.
6. Extract pooling station names, states, RE potentials, BESS capacities, connectivity parameters, margins, and GNA effectiveness exactly as printed.

Critical Anti-Hallucination Rules:
- ONLY extract values that are explicitly present in the provided text. Do NOT invent, guess, or estimate any value.
- If a value is genuinely not present or cannot be found in either the page text or table, return null. Never fill in a number that is not printed.
- Do NOT copy values from one row to another incorrectly. Each pooling station row is independent.
- Never extrapolate or infer numeric values. Extract only what is literally printed.
""".strip()


# =─────────────────────────────────────────────────────────────────────────────
# Filename date helper
# =─────────────────────────────────────────────────────────────────────────────

_FILENAME_DATE_RE = re.compile(r"(\d{2})[-_ ]?(\d{2})[-_ ]?(\d{4})")


def _date_from_filename(filename: str) -> str | None:
    """Extract DD-MM-YYYY from filename like '01_SS Margin 31 08 2025.pdf'."""
    m = _FILENAME_DATE_RE.search(filename)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return None


# =─────────────────────────────────────────────────────────────────────────────
# Text cleaning helper
# =─────────────────────────────────────────────────────────────────────────────

def clean_pdf_text(text: str) -> str:
    if not text:
        return ""
    # 1. Replace the weird "\t6" sequence with a clean en-dash "–"
    text = text.replace("\t6", "–")
    # 2. Remove null bytes (\u0000)
    text = text.replace("\u0000", "")
    # 3. Replace individual tabs with a space (so they don't break structural formatting)
    text = text.replace("\t", " ")
    # 4. Clean up multiple spaces
    text = re.sub(r" +", " ", text)
    return text.strip()


def _extract_page_bundle(page: Any) -> str:
    """Build a clean text bundle for the LLM from one pdfplumber page."""
    lines: list[str] = []

    raw_text = page.extract_text(x_tolerance=3, y_tolerance=3) or ""
    raw_text = clean_pdf_text(raw_text)
    lines.append("=== PAGE TEXT ===")
    lines.append(raw_text)
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
                    s = clean_pdf_text(s)
                    s = re.sub(r"[\r\n]+", " | ", s)
                    s = re.sub(r"[ \t]{2,}", " ", s)
                    cleaned.append(s)
            lines.append("\t".join(cleaned))
        lines.append("")

    return "\n".join(lines)


# =─────────────────────────────────────────────────────────────────────────────
# Agents (lazy singletons)
# =─────────────────────────────────────────────────────────────────────────────

_non_re_agent: Agent | None = None
_proposed_re_agent: Agent | None = None
_re_agent: Agent | None = None


def _get_agent(kind: str) -> Agent:
    global _non_re_agent, _proposed_re_agent, _re_agent
    ensure_api_key()
    model = get_model()

    if kind == "non-re":
        if _non_re_agent is None:
            _non_re_agent = Agent(
                model=model,
                output_type=NonRESubstationMarginResult,
                system_prompt=SYSTEM_PROMPT_NON_RE,
            )
            logger.info("Non-RE Agent ready.")
        return _non_re_agent
    elif kind == "proposed-re":
        if _proposed_re_agent is None:
            _proposed_re_agent = Agent(
                model=model,
                output_type=ProposedRESubstationMarginResult,
                system_prompt=SYSTEM_PROMPT_PROPOSED_RE,
            )
            logger.info("Proposed RE Agent ready.")
        return _proposed_re_agent
    elif kind == "re-substations":
        if _re_agent is None:
            _re_agent = Agent(
                model=model,
                output_type=RESubstationMarginResult,
                system_prompt=SYSTEM_PROMPT_RE,
            )
            logger.info("RE Substations Agent ready.")
        return _re_agent
    else:
        raise ValueError(f"Unknown margin PDF kind: {kind}")


# =─────────────────────────────────────────────────────────────────────────────
# Extraction functions
# =─────────────────────────────────────────────────────────────────────────────

def extract_margin_pdf(
    pdf_path: Path,
    kind: str,
    pages_per_chunk: int = 4,
) -> list[Any]:
    """
    Extract all margin records from a single margin PDF based on kind:
    - 'non-re'
    - 'proposed-re'
    - 're-substations'
    """
    agent = _get_agent(kind)
    filename = pdf_path.name
    filename_date = _date_from_filename(filename)
    detected_date: str | None = None
    all_records = []

    with pdfplumber.open(str(pdf_path)) as pdf:
        total = len(pdf.pages)
        logger.info("[%s] [%s] %d page(s)", kind.upper(), filename, total)

        bundles: list[str] = []
        for start in range(0, total, pages_per_chunk):
            parts: list[str] = []
            for i, page in enumerate(pdf.pages[start: start + pages_per_chunk], start + 1):
                parts.append(f"--- PAGE {i} of {total} ---")
                parts.append(_extract_page_bundle(page))
            bundles.append("\n".join(parts))

    logger.info("[%s] [%s] %d chunk(s) → LLM", kind.upper(), filename, len(bundles))

    for i, bundle in enumerate(bundles, 1):
        logger.info("[%s] [%s] chunk %d/%d", kind.upper(), filename, i, len(bundles))
        try:
            result = agent.run_sync(
                f"Extract all margin records:\n\n{bundle}"
            ).output

            if result.as_on_date and not detected_date:
                detected_date = result.as_on_date
            all_records.extend(result.records)

        except Exception as exc:
            err_str = str(exc)
            if "looping content" in err_str or "loop detection" in err_str.lower():
                logger.warning(
                    "[%s] [%s] chunk %d: loop detection triggered — retrying 1 page at a time",
                    kind.upper(), filename, i,
                )
                start_page = (i - 1) * pages_per_chunk
                end_page = min(start_page + pages_per_chunk, total)
                for pg_idx in range(start_page, end_page):
                    try:
                        with pdfplumber.open(str(pdf_path)) as pdf:
                            single_bundle = (
                                f"--- PAGE {pg_idx + 1} of {total} ---\n"
                                + _extract_page_bundle(pdf.pages[pg_idx])
                            )
                        sub_result = agent.run_sync(
                            f"Extract all margin records:\n\n{single_bundle}"
                        ).output
                        if sub_result.as_on_date and not detected_date:
                            detected_date = sub_result.as_on_date
                        all_records.extend(sub_result.records)
                    except Exception as sub_exc:
                        logger.error(
                            "[%s] [%s] page %d retry failed: %s",
                            kind.upper(), filename, pg_idx + 1, sub_exc, exc_info=True,
                        )
            else:
                logger.error("[%s] [%s] chunk %d failed: %s", kind.upper(), filename, i, exc, exc_info=True)

    # Finalize dates and inject metadata
    as_on = detected_date or filename_date
    final_records = []
    for rec in all_records:
        d = rec.model_dump()
        d["source_file"] = filename
        d["as_on_date"] = as_on
        # Re-initialize the model to validate
        if kind == "non-re":
            from app.re_margin_extraction.models import NonRESubstationMarginRecord
            final_records.append(NonRESubstationMarginRecord(**d))
        elif kind == "proposed-re":
            from app.re_margin_extraction.models import ProposedRESubstationMarginRecord
            final_records.append(ProposedRESubstationMarginRecord(**d))
        elif kind == "re-substations":
            from app.re_margin_extraction.models import RESubstationMarginRecord
            final_records.append(RESubstationMarginRecord(**d))

    logger.info("[%s] [%s] %d record(s) extracted", kind.upper(), filename, len(final_records))
    return final_records
