"""
Pydantic-AI agent module for CTUIL Renewable Energy Margin PDF extraction.

System prompts are built dynamically from each model's ``schema_info()``
classmethod so column names, nested paths and carry-forward rules are kept
in ONE place (models.py) and flow automatically into the prompts.

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
from shared.pdf_table_extractor import build_page_bundle
from app.re_margin_extraction.models import (
    NonRESubstationMarginResult,
    ProposedRESubstationMarginResult,
    RESubstationMarginResult,
    get_schema_info,
)

logger = logging.getLogger(__name__)

# =─────────────────────────────────────────────────────────────────────────────
# Dynamic system-prompt builder  (derives everything from schema_info)
# =─────────────────────────────────────────────────────────────────────────────

def _build_system_prompt(kind: str) -> str:
    """
    Build the full system prompt for a given PDF kind by introspecting the
    corresponding Pydantic model's schema_info().  No column names are
    hardcoded here — every detail is derived from the model.

    The generated prompt has four sections:
      1. Role + report-type header (from schema_info pdf_title / row_noun)
      2. Core extraction rules (universal + carry-forward rules from schema)
      3. CRITICAL nested-column-mapping block (built from nested_fields)
      4. Anti-hallucination rules
    """
    info = get_schema_info(kind)

    pdf_title  = info["pdf_title"]
    row_noun   = info["row_noun"]
    cf_fields  = info["carry_forward"]   # [(alias, description), …]
    nested     = info["nested_fields"]   # [(parent, [sub, …]), …] or
                                         # [(parent, [(mid, [leaf, …]), …]), …]

    # ── 1. Role header ──────────────────────────────────────────────────
    role = (
        f"You are a data extraction agent for {pdf_title} PDF reports.\n"
        f"Extract every {row_noun} row by reading BOTH the \""
        "=== PAGE TEXT ===\" and the \"=== TABLE ===\" sections."
    )

    # ── 2. Core rules ─────────────────────────────────────────────────
    # Build carry-forward rules dynamically from schema
    cf_rules: list[str] = []
    for i, (alias, desc) in enumerate(cf_fields, start=2):
        cf_rules.append(f"{i}. {desc}")

    num_start = len(cf_fields) + 2   # next rule number after carry-forward rules

    fixed_rules = [
        "1. \"=== PAGE TEXT ===\" is your primary source of truth. "
        "Use it to recover values when TABLE cells are empty or null.",
    ] + cf_rules + [
        f"{num_start}. Never skip any {row_noun} rows.",
        f"{num_start + 1}. If a cell contains '0' (zero), extract it as '0'. "
        "Do NOT convert '0' to null. Only blank cells, dashes '-', or spaces should be null.",
        f"{num_start + 2}. Extract all field values exactly as printed in the source.",
    ]
    rules_block = "Rules:\n" + "\n".join(fixed_rules)

    # ── 3. Nested-column-mapping block ───────────────────────────────────
    nested_lines: list[str] = [
        "CRITICAL — Nested Column Mapping (read carefully):",
        "The TABLE includes a \"[COLUMN PATHS]\" legend mapping Col N to nested JSON keys.",
        "Format:  Col N: Parent Column > Sub-column  (or Parent > Mid > Sub)",
        "You MUST use this legend to place each cell in the correct nested JSON object.",
        "Do NOT treat sub-column header rows as data rows — they are encoded in the legend.",
        "",
        "Expected nested column groups for this report type:",
    ]

    for parent, subs in nested:
        # subs is either [str, …] (one-level) or [(str, [str,…]), …] (two-level)
        if subs and isinstance(subs[0], tuple):
            # Two-level nesting (e.g. Transformation Capacity > Existing > 765/400kV)
            for mid, leaves in subs:
                if leaves:
                    for leaf in leaves:
                        nested_lines.append(f"  {parent} > {mid} > {leaf}")
                else:
                    nested_lines.append(f"  {parent} > {mid}")
        else:
            # One-level nesting
            for sub in subs:
                nested_lines.append(f"  {parent} > {sub}")

    nested_lines += [
        "",
        "Example: a column path of \"X > Y\" means the cell value belongs at:",
        "  { \"X\": { \"Y\": <value> } }    in the output JSON.",
    ]
    nested_block = "\n".join(nested_lines)

    # ── 4. Anti-hallucination rules ────────────────────────────────────
    anti_halluc = (
        "Anti-Hallucination Rules:\n"
        "- ONLY extract values explicitly present in the text. Never invent or estimate.\n"
        "- If a value is genuinely absent, return null. Never fill in a number not printed.\n"
        f"- Each {row_noun} row is independent — never copy values between rows.\n"
        "- Never extrapolate or infer numeric values."
    )

    return "\n\n".join([role, rules_block, nested_block, anti_halluc])


# Warm-up cache: build prompts once at import time and store them.
# This avoids rebuilding on every agent creation call.
_PROMPT_CACHE: dict[str, str] = {}


def _get_system_prompt(kind: str) -> str:
    if kind not in _PROMPT_CACHE:
        _PROMPT_CACHE[kind] = _build_system_prompt(kind)
        logger.debug("[%s] system prompt built (%d chars)", kind, len(_PROMPT_CACHE[kind]))
    return _PROMPT_CACHE[kind]



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
# Agents (lazy singletons)
# =─────────────────────────────────────────────────────────────────────────────

_non_re_agent: Agent | None = None
_proposed_re_agent: Agent | None = None
_re_agent: Agent | None = None


def _get_agent(kind: str) -> Agent:
    global _non_re_agent, _proposed_re_agent, _re_agent
    ensure_api_key()
    model = get_model()
    system_prompt = _get_system_prompt(kind)

    if kind == "non-re":
        if _non_re_agent is None:
            _non_re_agent = Agent(
                model=model,
                output_type=NonRESubstationMarginResult,
                system_prompt=system_prompt,
            )
            logger.info("Non-RE Agent ready.")
        return _non_re_agent
    elif kind == "proposed-re":
        if _proposed_re_agent is None:
            _proposed_re_agent = Agent(
                model=model,
                output_type=ProposedRESubstationMarginResult,
                system_prompt=system_prompt,
            )
            logger.info("Proposed RE Agent ready.")
        return _proposed_re_agent
    elif kind == "re-substations":
        if _re_agent is None:
            _re_agent = Agent(
                model=model,
                output_type=RESubstationMarginResult,
                system_prompt=system_prompt,
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
                parts.append(build_page_bundle(page, i, total))
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
                            single_bundle = build_page_bundle(pdf.pages[pg_idx], pg_idx + 1, total)
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
