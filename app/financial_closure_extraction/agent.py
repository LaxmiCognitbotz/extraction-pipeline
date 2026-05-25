"""
Pydantic-AI agent for CTUIL Compliance PDF table extraction.

Provider : shared.llm.get_model()  (LLM_PROVIDER=vm → Azure via llm_client.bat)
Schema   : output_type=PageExtractionResult  (Pydantic injects full field contract)
Prompt   : minimal — only what the schema cannot express
"""

from __future__ import annotations

import datetime
import logging
import re
from pathlib import Path
from typing import Any

import pdfplumber
from pydantic_ai import Agent

from shared.llm import get_model, ensure_api_key
from app.financial_closure_extraction.models import (
    FCDeadlineTable,
    FileExtractionResult,
    LandDocDeadlineTable,
    PageExtractionResult,
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# System prompt — minimal by design
#
# WHY SO SHORT?
#   Pydantic-AI already injects the full JSON schema (field names, types,
#   Field descriptions) into the LLM call via output_type=PageExtractionResult.
#   The Field(description=...) strings on every model field carry the field-level
#   instructions.  Python post-processing handles Excel serial conversion.
#   This prompt covers ONLY the three things the schema cannot express:
#     1. Verbatim copy behaviour + null rule
#     2. Table classification (FC vs Land Doc) from the heading line
#     3. report_period format + "Spet" typo
# ─────────────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """
Extract every table row from the PDF content exactly as printed.
Follow the Pydantic output schema — its field names and descriptions are your guide.

- Copy all values verbatim. Never summarise, infer, or skip any row.
- Blank / dash cell → null.
- Multi-line cell → join with one space.
- 5-digit integer in a date field = Excel serial → return "DD-MM-YYYY"
  (formula: 1899-12-30 + serial days). Never return a raw integer.
- Heading contains "Financial Closure" → FCDeadlineTable (due_date_of_fc).
- Heading contains "Land Document" or "Land Doc" → LandDocDeadlineTable
  (due_date_for_submission_of_land_docs).
- table_name from sub-heading: fc_deadline_transition_cases /
  fc_deadline_gna_regulation / fc_deadline_main / land_doc_deadline_main.
- report_period from title "from X to Y YYYY" → "X YYYY - Y YYYY".
  Known typo: "Spet" = September.
""".strip()


# ─────────────────────────────────────────────────────────────────────────────
# PDF page → structured text bundle
# ─────────────────────────────────────────────────────────────────────────────

def _extract_page_bundle(page: Any) -> str:
    """
    Build a text bundle for the LLM from a single pdfplumber page.

    Two sections:
      === PAGE TEXT ===          raw text  (for heading/title detection)
      === TABLE N (cells) ===   tab-separated cell matrix (for data rows)
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
                    s = re.sub(r"[\r\n]+", " ", s)
                    s = re.sub(r"[ \t]{2,}", " ", s)
                    cleaned.append(s)
            lines.append("\t".join(cleaned))
        lines.append("")

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Excel serial → DD-MM-YYYY  (Python safety net)
# Applied after every agent call in case any integer slips through.
# ─────────────────────────────────────────────────────────────────────────────

_SERIAL_RE = re.compile(r"^\d{5}$")
_EPOCH = datetime.date(1899, 12, 30)

_DATE_FIELDS: frozenset[str] = frozenset({
    "submission_date",
    "scod_as_per_application",
    "first_scod_of_generation_project",
    "revised_scod",
    "date_of_connectivity_intimation_in_principle",
    "date_of_connectivity_intimation_final",
    "connectivity_gna_start_date_in_principle",
    "connectivity_gna_start_date_firm",
    "due_date_of_fc",
    "due_date_for_submission_of_land_docs",
})


def _fix_serial(value: str | None) -> str | None:
    if value is None:
        return None
    s = value.strip()
    if _SERIAL_RE.fullmatch(s):
        try:
            return (_EPOCH + datetime.timedelta(days=int(s))).strftime("%d-%m-%Y")
        except (ValueError, OverflowError):
            pass
    return s


def _fix_dates(row_dict: dict) -> dict:
    return {k: (_fix_serial(v) if k in _DATE_FIELDS else v) for k, v in row_dict.items()}


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
) -> FileExtractionResult:
    """
    Extract all compliance tables from a CTUIL PDF using the LLM agent.

    Args:
        pdf_path:        Path to the PDF.
        pages_per_chunk: Pages per LLM call (reduce to 2-3 on low-context VMs).
    """
    agent = _get_agent()
    all_fc: list[FCDeadlineTable] = []
    all_land: list[LandDocDeadlineTable] = []
    period = "Unknown Period"

    # Build page bundles with pdfplumber
    with pdfplumber.open(str(pdf_path)) as pdf:
        total = len(pdf.pages)
        logger.info("[%s]  %d page(s)", pdf_path.name, total)
        bundles: list[str] = []
        for start in range(0, total, pages_per_chunk):
            parts: list[str] = []
            for i, page in enumerate(pdf.pages[start: start + pages_per_chunk], start + 1):
                parts.append(f"--- PAGE {i} of {total} ---")
                parts.append(_extract_page_bundle(page))
            bundles.append("\n".join(parts))

    logger.info("[%s]  %d chunk(s) → LLM", pdf_path.name, len(bundles))

    for i, bundle in enumerate(bundles, 1):
        logger.info("[%s]  chunk %d/%d", pdf_path.name, i, len(bundles))
        try:
            result: PageExtractionResult = agent.run_sync(
                f"Extract all compliance tables:\n\n{bundle}"
            ).output
        except Exception as exc:  # noqa: BLE001
            logger.error("[%s] chunk %d failed: %s", pdf_path.name, i, exc, exc_info=True)
            continue

        if result.report_period and result.report_period != "Unknown Period":
            period = result.report_period

        for tbl in result.fc_tables:
            tbl.rows = [
                type(r)(**{**_fix_dates(r.model_dump()), "report_period": period})
                for r in tbl.rows
            ]
            all_fc.append(tbl)

        for tbl in result.land_doc_tables:
            tbl.rows = [
                type(r)(**{**_fix_dates(r.model_dump()), "report_period": period})
                for r in tbl.rows
            ]
            all_land.append(tbl)

    tables = all_fc + all_land
    logger.info("[%s]  %d table(s), %d row(s)", pdf_path.name, len(tables), sum(len(t.rows) for t in tables))

    return FileExtractionResult(report_period=period, source_file=pdf_path.name, tables=tables)
