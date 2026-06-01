"""
LangExtract-powered extractor for CTUIL Revocation (24.6) PDFs.

Uses google/langextract with the project's Azure OpenAI VM endpoint so that
all extractions flow through the same llm_client.bat credential source as the
rest of the pipeline.

Each PDF is processed page-by-page (or optionally in small chunks).
The structured rows are assembled into RevocationRecord-compatible dicts
which are then validated against the existing Pydantic models.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

import langextract as lx
from langextract.factory import ModelConfig

from shared.llm import _parse_bat_credentials, _get_bat_path, _load_dotenv

logger = logging.getLogger(__name__)

# ── Load env / credentials ────────────────────────────────────────────────────
_load_dotenv()


def _build_model_config() -> ModelConfig:
    """
    Build a langextract ModelConfig that points to the project's Azure OpenAI
    VM endpoint (the same credentials as llm_client.bat).
    """
    bat_path = _get_bat_path()
    creds = _parse_bat_credentials(bat_path)

    base_url = (
        f"{creds['endpoint'].rstrip('/')}"
        f"/openai/deployments/{creds['deployment']}"
        f"/chat/completions?api-version={creds['api_version']}"
    )
    # langextract's OpenAI provider accepts an explicit base_url + api_key
    # We set the base_url to the Azure deployment's chat endpoint so that
    # langextract routes through the VM.
    azure_base = f"{creds['endpoint'].rstrip('/')}"

    return ModelConfig(
        model_id=creds["deployment"],
        provider="openai",
        provider_kwargs={
            "api_key": creds["api_key"],
            "base_url": f"{azure_base}/openai/deployments/{creds['deployment']}",
            # Azure needs the api-version as a query param — pass it via the
            # default_query_params mechanism supported by the openai SDK
            "default_query": {"api-version": creds["api_version"]},
        },
    )


# ── Prompts ───────────────────────────────────────────────────────────────────

PROMPT_DESCRIPTION = """
You are extracting rows from a CTUIL Regulation 24.6 Revocation compliance table.
Each row represents one application. Extract every application row — never skip one.

COLUMN MAPPING (use these names exactly):
  - "application_id": The application/LTA ID (e.g. "LTA/2020/123"). Extract only the ID part; do NOT include the applicant name.
  - "applicant_name": Full name of the applicant/company. Do NOT include the application ID here.
  - "region": Region (e.g. "Northern Region", "Western Region"). Carry forward from section headers.
  - "criterion": The regulation criterion cited (e.g. "24.6(a)", "24.6(b)").
  - "type_of_project": E.g. "Solar", "Wind", "Hybrid".
  - "present_connectivity_gna_mw": A numeric MW value ONLY (e.g. "250"). NEVER a date string.
  - "substation": The ISTS substation name for the connection.
  - "gna_start_date": Date of GNA commencement (e.g. "01-Apr-22") or null.
  - "connectivity_status": Status text like "Effective", "Not Effective". NEVER a date.
  - "date_effective": When connectivity became effective (a date string) or null.
  - "commission_status": E.g. "Commissioned", "Not commissioned".
  - "scod_as_per_application": A date string (e.g. "31-Mar-25") or null. NEVER "0" or a number.
  - "updated_revised_scod": A date or "NA" or null.
  - "compliance_due_date": A date string. NEVER a status text.

ANTI-HALLUCINATION RULES:
- Only extract values literally present in the text. Never invent or estimate.
- If a cell is blank, dash "-", or a space, return null.
- "0" is never a valid date. If you see "0" in a date column, return null.
- If a date appears in the MW field, the row has shifted — re-read from PAGE TEXT and re-align.
- Each row is completely independent; never copy values between rows.
""".strip()


EXAMPLE_TEXT = """
Sr. No. | Application ID / Applicant Name | Region | Criterion | Type | MW | Substation | GNA Start | Status | Date Eff. | Commission | SCOD App. | Updated SCOD | Due Date
1 | LTA/2021/001 Sunshine Energy Ltd | Northern Region | 24.6(a) | Solar | 250 | Fatehpur | 01-Apr-22 | Effective | 15-Apr-22 | Commissioned | 31-Mar-24 | NA | 30-Jun-24
""".strip()

EXAMPLES = [
    lx.data.ExampleData(
        text=EXAMPLE_TEXT,
        extractions=[
            lx.data.Extraction(
                extraction_class="revocation_row",
                extraction_text="LTA/2021/001 Sunshine Energy Ltd",
                attributes={
                    "application_id": "LTA/2021/001",
                    "applicant_name": "Sunshine Energy Ltd",
                    "region": "Northern Region",
                    "criterion": "24.6(a)",
                    "type_of_project": "Solar",
                    "present_connectivity_gna_mw": "250",
                    "substation": "Fatehpur",
                    "gna_start_date": "01-Apr-22",
                    "connectivity_status": "Effective",
                    "date_effective": "15-Apr-22",
                    "commission_status": "Commissioned",
                    "scod_as_per_application": "31-Mar-24",
                    "updated_revised_scod": "NA",
                    "compliance_due_date": "30-Jun-24",
                },
            )
        ],
    )
]


# ── Regex helpers ─────────────────────────────────────────────────────────────

_UPTO_RE = re.compile(
    r"(?:upto|Final\s+list)\s+([A-Za-z]+['\u2019\u2018]?\d{2})",
    re.IGNORECASE,
)


def _detect_upto_month(text: str) -> str | None:
    m = _UPTO_RE.search(text)
    if m:
        return m.group(1).replace("\u2019", "'").replace("\u2018", "'")
    return None


# ── LangExtract extraction ────────────────────────────────────────────────────

def _extract_rows_from_page(
    page_text: str,
    model_cfg: ModelConfig,
    pdf_name: str,
    page_num: int,
) -> list[dict[str, Any]]:
    """
    Run langextract on a single page bundle and return a list of row dicts.
    Each dict maps field names to extracted string values (or None).
    """
    try:
        result = lx.extract(
            text_or_documents=page_text,
            prompt_description=PROMPT_DESCRIPTION,
            examples=EXAMPLES,
            config=model_cfg,
            # For complex structured tables we want 1 extraction pass per chunk
            extraction_passes=1,
            max_workers=1,
        )
    except Exception as exc:
        logger.error(
            "[%s] page %d langextract failed: %s", pdf_name, page_num, exc, exc_info=True
        )
        return []

    rows: list[dict[str, Any]] = []
    for extraction in result.extractions:
        if extraction.extraction_class != "revocation_row":
            continue
        attrs = extraction.attributes or {}
        row: dict[str, Any] = {
            "application_id": attrs.get("application_id"),
            "applicant_name": attrs.get("applicant_name"),
            "region": attrs.get("region"),
            "criterion": attrs.get("criterion"),
            "type_of_project": attrs.get("type_of_project"),
            "present_connectivity_gna_mw": attrs.get("present_connectivity_gna_mw"),
            "substation": attrs.get("substation"),
            "gna_start_date": attrs.get("gna_start_date"),
            "connectivity_status": attrs.get("connectivity_status"),
            "date_effective": attrs.get("date_effective"),
            "commission_status": attrs.get("commission_status"),
            "scod_as_per_application": attrs.get("scod_as_per_application"),
            "updated_revised_scod": attrs.get("updated_revised_scod"),
            "compliance_due_date": attrs.get("compliance_due_date"),
        }
        # Validate: scod must never be "0"
        if row["scod_as_per_application"] == "0":
            row["scod_as_per_application"] = None
        # Validate: MW must be numeric
        mw = row["present_connectivity_gna_mw"]
        if mw and re.search(r"[A-Za-z\-]{3,}", mw):
            logger.warning(
                "[%s] page %d — MW field looks like a date ('%s'), nulling it",
                pdf_name, page_num, mw,
            )
            row["present_connectivity_gna_mw"] = None

        rows.append(row)

    logger.debug("[%s] page %d → %d rows via langextract", pdf_name, page_num, len(rows))
    return rows


# ── Public entry point ────────────────────────────────────────────────────────

def extract_pdf(
    pdf_path: Path,
    page_bundles: list[tuple[int, str]],
) -> list[dict[str, Any]]:
    """
    Extract all revocation rows from a single PDF.

    Args:
        pdf_path:     Path to the PDF (for logging / metadata).
        page_bundles: List of (page_number, bundle_text) from pdf_reader.py.

    Returns:
        List of flat dicts, one per extracted row, with 'source_file' and
        'upto_month' injected.
    """
    pdf_name = pdf_path.name
    model_cfg = _build_model_config()
    detected_upto: str | None = None
    all_rows: list[dict[str, Any]] = []

    logger.info("[%s] starting langextract over %d page(s)", pdf_name, len(page_bundles))

    for page_num, bundle_text in page_bundles:
        # Try to detect upto_month from page text
        if not detected_upto:
            detected_upto = _detect_upto_month(bundle_text)

        rows = _extract_rows_from_page(bundle_text, model_cfg, pdf_name, page_num)
        all_rows.extend(rows)

    # Inject metadata
    for row in all_rows:
        row["source_file"] = pdf_name
        row["upto_month"] = detected_upto

    logger.info("[%s] total %d rows extracted", pdf_name, len(all_rows))
    return all_rows
