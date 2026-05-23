"""Camelot table data → structured JSON via PydanticAI.

Uses ``list[TransmissionElement]`` as the output_type — the Pydantic
field descriptions ARE the extraction instructions.  No separate
system_prompt.md file needed.

Supports three LLM backends: groq, vm (Azure), google (Gemini).

Architecture:
  1. Camelot CSV corpus is smart-chunked (~6K chars each)
  2. Each chunk → PydanticAI Agent → list[TransmissionElement]
  3. Partial element lists are merged across chunks
  4. Business logic post-processing applied
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic_ai import Agent
from pydantic_ai.settings import ModelSettings

from app.tbcb_extraction.business_logic import post_process_elements
from app.tbcb_extraction.config import settings
from shared.llm import ensure_api_key, get_model
from app.tbcb_extraction.schemas import (
    DocType,
    ExtractionResult,
    TransmissionElement,
)

if TYPE_CHECKING:
    from app.tbcb_extraction.converter import CamelotCorpus


# ── System Prompt (short — schema descriptions do the heavy lifting) ──

_SYSTEM_PROMPT = """\
You are a data extraction agent for Indian power transmission reports (CEA/CTUIL).

You receive CSV-formatted table data extracted from TBCB or RTM monthly progress reports.

The tables have a parent-child structure:
- Parent rows: numbered (1, 2, 3…), contain the FULL transmission scheme/project name
  (e.g. "Transmission system for evacuation of power from REZ in Rajasthan (20GW) under Phase-III Part F"),
  executing agency, SPV Transfer Date, original/anticipated SCOD.
- Child rows: specific elements (lines, substations, ICTs, bays) with physical progress and remarks.

CRITICAL — Distinguishing Scheme vs Scope:
- transmission_scheme = FULL project/scheme name. Always starts with words like
  "Transmission system/scheme for...", "Augmentation of...", "System Strengthening...",
  "Establishment of...". These are long descriptive project names.
- transmission_scope  = SPECIFIC element being constructed. Examples:
  "Fatehgarh3– Beawar 765kV D/C line", "2x1500MVA, 765/400KV GIS substation at Narela",
  "LILO of both circuits of ...", "2x300 MVAr Statcom at ...".
  These describe physical assets (lines, substations, ICTs, bays).
- If a text mentions kV lines, MVA substations, D/C, S/C, LILO, ICT, bay, STATCOM
  → it is a SCOPE, NOT a scheme. Never put scope text in the scheme field.

CRITICAL — Page Breaks:
- Tables span multiple pages. A scheme (parent row) may appear at the BOTTOM of one
  page, and its child scope rows continue at the TOP of the next page/table.
- If the first rows in a chunk have NO parent scheme row above them, they are
  continuation children from the previous page. Leave their transmission_scheme EMPTY.
  Post-processing will inherit the correct scheme.
- NEVER invent or guess a scheme name. If unsure, leave transmission_scheme empty.

Rules:
- Extract every row as one TransmissionElement.
- For child rows, leave transmission_scheme empty (post-processing handles inheritance).
- Substations/ICTs: fill ss_civil_work_pct, ss_equipment_received_pct, ss_equipment_erected_pct. Leave tx_* fields null.
- Transmission lines: fill tx_length, tx_location, tx_foundation, tx_erection, tx_stringing. Leave ss_* fields null.
- Percentages: extract exactly as written (e.g., '92.00%'). Do NOT convert to decimal.
- MVA: compute total from scope text. "3x1500MVA" → 4500. Only for substations/ICTs.
- If a value is missing, use null for numbers, "" for strings.
- Do NOT hallucinate. Extract only what is present.
- Do NOT extract summary rows, grand totals, or headers (e.g., "PGCIL", "Private TSPs", "Total", "Grand Total"). Extract ONLY actual transmission elements.
- Leave element_code, inter_intra_tx_element, status, source, tx_foundation_pct, tx_erection_pct, tx_stringing_pct empty/null — they are computed by post-processing.
- Tentative SCOD: ALWAYS extract this from the parent row. Look for column named exactly "Tentative SCOD". Format: "MMM-YY".
- SPV Transfer Date: ALWAYS extract this from the parent row. Look for columns like
  "SPV Transfer/ Award Date" or "Date of Transfer of SPV". Format: "MMM-YY" (e.g. "Sep-23", "Dec-23").
  Inherit to all child rows under the same scheme.
- Extract ALL rows. Completeness is critical.
"""


# ── Agent Factory ──────────────────────────────────────────────────────


def _get_agent() -> Agent[None, list[TransmissionElement]]:
    """Create the extraction Agent.

    Uses ``list[TransmissionElement]`` as output_type — the Pydantic
    field descriptions tell the model exactly what to extract.
    """
    ensure_api_key()
    model = get_model()

    max_tokens = 16384
    timeout = 300
    if settings.llm_provider == "google":
        max_tokens = 65536
    elif settings.llm_provider == "groq":
        max_tokens = 16384
        timeout = 180
    elif settings.llm_provider == "vm":
        max_tokens = 16384
        timeout = 300

    return Agent(
        model=model,
        output_type=list[TransmissionElement],
        system_prompt=_SYSTEM_PROMPT,
        retries=settings.agent_retries,
        model_settings=ModelSettings(
            temperature=settings.temperature,
            max_tokens=max_tokens,
            timeout=timeout,
        ),
    )


# ── User Message Builder ──────────────────────────────────────────────


def _build_user_message(
    content: str,
    doc_type: DocType,
    region: str = "",
    chunk_info: str = "",
    last_known_scheme: str = "",
    last_known_spv_date: str = "",
    last_known_tentative_scod: str = "",
) -> str:
    """Build user message with doc-type header and table data."""
    header = f"DOC_TYPE: {doc_type.value}"
    if region:
        header += f"\nREGION: {region}"
    if chunk_info:
        header += f"\n{chunk_info}"
    if last_known_scheme:
        header += f"\nLAST_KNOWN_TRANSMISSION_SCHEME_FROM_PREV_PAGE: '{last_known_scheme}'"
        header += (
            "\nIMPORTANT: If the first rows in this chunk have NO parent scheme row "
            "(no numbered row with a full scheme name like 'Transmission system for...'), "
            "they are CONTINUATION children from the previous page. "
            "Leave their transmission_scheme EMPTY. "
            "Do NOT put their scope text (e.g. line/substation descriptions) into the "
            "transmission_scheme field."
        )
    if last_known_spv_date:
        header += f"\nLAST_KNOWN_SPV_TRANSFER_DATE: '{last_known_spv_date}'"
        header += "\nINSTRUCTION: If child rows have no SPV Transfer Date, leave it EMPTY (handled by post-processing)."
    if last_known_tentative_scod:
        header += f"\nLAST_KNOWN_TENTATIVE_SCOD: '{last_known_tentative_scod}'"
        header += "\nINSTRUCTION: If child rows have no Tentative SCOD, leave it EMPTY (handled by post-processing)."
    return f"{header}\n\n{content}"


# ── Single-Chunk Extraction ───────────────────────────────────────────


def _extract_chunk(
    agent: Agent[None, list[TransmissionElement]],
    chunk_text: str,
    chunk_index: int,
    total_chunks: int,
    doc_type: DocType,
    region: str = "",
    last_known_scheme: str = "",
    last_known_spv_date: str = "",
    last_known_tentative_scod: str = "",
) -> list[TransmissionElement]:
    """Extract elements from one chunk with transport-level retries."""
    chunk_info = f"CHUNK: {chunk_index} of {total_chunks}"
    user_message = _build_user_message(
        chunk_text, doc_type, region, chunk_info, last_known_scheme, last_known_spv_date, last_known_tentative_scod
    )

    max_retries = 3
    last_error = None

    for attempt in range(1, max_retries + 1):
        try:
            result = agent.run_sync(user_message)
            _print_usage(result)
            return result.output

        except Exception as e:
            error_name = type(e).__name__
            is_transport = any(
                kw in error_name.lower() or kw in str(e).lower()
                for kw in ("remoteprotocol", "readtimeout", "disconnect",
                           "connection", "timeout")
            )
            if is_transport and attempt < max_retries:
                wait = 2 ** attempt * 5
                print(
                    f"[extractor]   [RETRY] Chunk {chunk_index}: "
                    f"{error_name} attempt {attempt}. Wait {wait}s ..."
                )
                time.sleep(wait)
                last_error = e
                continue
            raise

    raise last_error  # type: ignore


# ── Main Entry Point: Corpus → Elements ───────────────────────────────


def extract_from_corpus(
    corpus: "CamelotCorpus",
    doc_type: str | DocType,
    region: str = "",
    source_pdf: str = "",
) -> ExtractionResult:
    """Extract elements from a Camelot corpus (chunk-by-chunk).

    Args:
        corpus: ``CamelotCorpus`` from the converter.
        doc_type: Document type identifier.
        region: Optional region.
        source_pdf: Source PDF filename.

    Returns:
        ``ExtractionResult`` with post-processed elements.
    """
    if isinstance(doc_type, str):
        doc_type = DocType(doc_type)

    chunks = corpus.chunks
    if not chunks:
        print("[extractor] No chunks to process")
        return ExtractionResult(doc_type=doc_type, region=region,
                                source_pdf=source_pdf)

    total_chunks = len(chunks)
    total_chars = sum(len(c) for c in chunks)
    print(
        f"[extractor] {total_chunks} chunk(s) "
        f"({total_chars:,} chars) -> {settings.model_name} "
        f"({settings.llm_provider})"
    )

    agent = _get_agent()
    all_elements: list[TransmissionElement] = []
    start_time = time.time()
    
    last_known_scheme = ""
    last_known_spv_date = ""
    last_known_tentative_scod = ""

    for i, chunk in enumerate(chunks, 1):
        if not chunk.strip():
            continue
        print(f"[extractor]   [{i}/{total_chunks}] ({len(chunk):,} chars) ...")

        try:
            elems = _extract_chunk(
                agent, chunk, i, total_chunks, doc_type, region,
                last_known_scheme, last_known_spv_date, last_known_tentative_scod
            )
            all_elements.extend(elems)
            
            # Update last_known_scheme and last_known_spv_date for the next chunk
            for elem in reversed(elems):
                if elem.transmission_scheme and elem.transmission_scheme.strip():
                    last_known_scheme = elem.transmission_scheme.strip()
                    break
            for elem in reversed(elems):
                if elem.spv_transfer_date and elem.spv_transfer_date.strip():
                    last_known_spv_date = elem.spv_transfer_date.strip()
                    break
            for elem in reversed(elems):
                if elem.tentative_scod and elem.tentative_scod.strip():
                    last_known_tentative_scod = elem.tentative_scod.strip()
                    break
                    
            print(f"[extractor]   [{i}/{total_chunks}] -> {len(elems)} elements")
        except Exception as e:
            print(f"[extractor]   [{i}/{total_chunks}] [ERR] {e}")
            continue

    elapsed = time.time() - start_time
    print(
        f"[extractor] [OK] {len(all_elements)} elements "
        f"from {total_chunks} chunks in {elapsed:.1f}s"
    )

    # Post-processing: codes, status, percentages, inter_intra, etc.
    all_elements = post_process_elements(all_elements, doc_type)

    return ExtractionResult(
        doc_type=doc_type,
        region=region,
        source_pdf=source_pdf,
        source_markdown="(camelot chunked extraction)",
        element_count=len(all_elements),
        elements=all_elements,
    )


# ── Legacy: Extract from existing Markdown ────────────────────────────


def extract_elements(
    markdown_path: str | Path,
    doc_type: str | DocType,
    region: str = "",
) -> ExtractionResult:
    """Extract from an existing Markdown file (--extract-only mode)."""
    md_path = Path(markdown_path).resolve()
    if not md_path.exists():
        raise FileNotFoundError(f"Markdown not found: {md_path}")

    if isinstance(doc_type, str):
        doc_type = DocType(doc_type)

    md_content = md_path.read_text(encoding="utf-8")
    print(f"[extractor] Read {md_path.name} ({len(md_content):,} chars)")

    from app.tbcb_extraction.converter import chunk_text
    chunks = chunk_text(md_content)
    print(f"[extractor] {len(chunks)} chunk(s) -> {settings.model_name}")

    agent = _get_agent()
    all_elements: list[TransmissionElement] = []
    start_time = time.time()

    for i, chunk in enumerate(chunks, 1):
        print(f"[extractor]   [{i}/{len(chunks)}] ({len(chunk):,} chars) ...")
        try:
            elems = _extract_chunk(
                agent, chunk, i, len(chunks), doc_type, region
            )
            all_elements.extend(elems)
        except Exception as e:
            print(f"[extractor]   [{i}/{len(chunks)}] [ERR] {e}")
            continue

    elapsed = time.time() - start_time
    print(f"[extractor] [OK] {len(all_elements)} elements in {elapsed:.1f}s")

    all_elements = post_process_elements(all_elements, doc_type)

    return ExtractionResult(
        doc_type=doc_type, region=region,
        source_markdown=str(md_path),
        element_count=len(all_elements),
        elements=all_elements,
    )


# ── Utilities ──────────────────────────────────────────────────────────


def _print_usage(result) -> None:
    """Print token usage if available."""
    try:
        usage = result.usage()
        if usage:
            parts = []
            if usage.request_tokens:
                parts.append(f"in={usage.request_tokens:,}")
            if usage.response_tokens:
                parts.append(f"out={usage.response_tokens:,}")
            if parts:
                print(f"[extractor]     Tokens: {', '.join(parts)}")
    except Exception:
        pass
