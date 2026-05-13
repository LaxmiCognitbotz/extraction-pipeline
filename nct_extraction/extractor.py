"""NCT PDF → structured JSON extraction.

Smart page filtering: only pages with scope/scheme keywords go to the LLM.
Uses pdfplumber for text + camelot for bordered tables.

Usage:
    python -m nct_extraction <pdf_path>                   # single PDF
    python -m nct_extraction uploads/CEA-NCT-Minutes/     # entire directory
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path
from typing import List, Optional

import pdfplumber
from pydantic_ai import Agent
from pydantic_ai.settings import ModelSettings

from nct_extraction.schemas import NCTElement, NCTExtractionResult

# ── Keywords to detect scope pages ───────────────────────────────────
# If ANY of these appear on a page, we include that page's text/tables.

SCOPE_KEYWORDS = [
    "scope of the transmission",
    "scope of transmission",
    "scope of works",
    "scope of work",
    "scope of the scheme",
    "estimated cost",
    "implementation timeline",
    "implementation time-frame",
    "implementation timeframe",
    "capacity /km",
    "capacity/km",
    "capacity (mva)",
    "capacity/ckm",
    "capacity /ckm",
]

# Table header keywords — if a table header contains these, we include it
TABLE_HEADER_KEYWORDS = [
    "scope", "cost", "capacity", "timeline", "timeframe",
    "implementing", "estimated", "schedule",
]

# ── Lazy agent init ──────────────────────────────────────────────────

_agent: Agent | None = None


def _get_agent() -> Agent[None, list[NCTElement]]:
    """Create the Pydantic AI agent using whatever LLM provider is configured."""
    global _agent
    if _agent is not None:
        return _agent

    project_root = str(Path(__file__).parent.parent)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    from app.llm import get_model, ensure_api_key
    ensure_api_key()
    model = get_model()

    _agent = Agent(
        model=model,
        output_type=list[NCTElement],
        system_prompt=_SYSTEM_PROMPT,
        retries=2,
        model_settings=ModelSettings(
            temperature=0.0,
            max_tokens=16384,
            timeout=300,
        ),
    )
    return _agent


_SYSTEM_PROMPT = """\
You are a data extraction agent for CEA NCT (National Committee on Transmission) meeting minutes.

You will receive ONLY the relevant page text and table data that contains transmission scheme scope information.
Each page has already been filtered by keywords — every page you see contains real data.

EXTRACT every element from "Scope of Transmission Scheme" or "Scope of Works" tables.

RULES:
1. Each table row = one NCTElement.
2. scheme_name = the PARENT heading above the table (e.g. "Transmission system for evacuation of power from REZ in Rajasthan").
   Apply the SAME scheme_name to ALL scope rows under it.
3. scope = the physical element description from the "Scope" column (lines, substations, bays, reactors, ICTs).
   Keep scope SEPARATE from scheme_name.
4. capacity_mva: compute total. "3x1500MVA" = 4500.0. Only for substations/ICTs. null for lines.
5. length_km: from "Capacity/km" or "Length" column. Only for lines. null for substations.
6. project_cost_cr: from "Estimated Cost" / "Cost" columns. May be per-element or per-scheme.
7. execution_timeline: from "Timeline" / "Schedule" / "Timeframe" columns.
8. tender_issuing_authority: from "Implementing Agency" / "BPC" columns.
9. implementation_mode: "TBCB" or "RTM" if stated.
10. element_code: Sl.No. / Sr.No. from the table.
11. Leave source empty — post-processing fills it.
12. Do NOT hallucinate. Only extract what is explicitly in the text.
13. Do NOT extract attendee lists, meeting procedures, or non-scope tables.
14. If scheme heading says "Name of Scheme:" before the scope table, that IS the scheme_name.
"""


# ── Page Extraction ──────────────────────────────────────────────────


def _extract_relevant_pages(pdf_path: str) -> list[dict]:
    """Go through EACH page. If scope keywords found, grab that page.

    Two-tier filtering:
      - STRONG keywords (scope of the transmission, scope of works) → always include
      - WEAK keywords (estimated cost, capacity) → include only if page has a relevant table

    Returns list of {page: int, text: str, tables_text: str, has_table: bool}
    """
    STRONG = [
        "scope of the transmission",
        "scope of transmission",
        "scope of works",
        "scope of work",
        "scope of the scheme",
    ]
    WEAK = [
        "estimated cost",
        "implementation timeline",
        "implementation time-frame",
        "implementation timeframe",
        "capacity /km",
        "capacity/km",
        "capacity (mva)",
        "capacity/ckm",
        "capacity /ckm",
    ]

    relevant = []

    with pdfplumber.open(pdf_path) as pdf:
        total = len(pdf.pages)
        for i, page in enumerate(pdf.pages):
            text = page.extract_text() or ""
            if not text.strip():
                continue

            lower = text.lower()

            has_strong = any(kw in lower for kw in STRONG)
            has_weak = any(kw in lower for kw in WEAK)

            if not has_strong and not has_weak:
                continue

            # Extract tables from this page
            tables_text = ""
            has_table = False
            tables = page.extract_tables() or []

            for j, table in enumerate(tables):
                if not table or len(table) < 2:
                    continue

                # Try to find the real header row — some tables have
                # merged cells where pdfplumber gives empty first row
                header_row = table[0]
                header_str = " ".join(str(c) for c in header_row if c).lower()

                # If >50% of header cells are empty, try row 1
                empty_ratio = sum(1 for c in header_row if not c or not str(c).strip()) / max(len(header_row), 1)
                if empty_ratio > 0.5 and len(table) > 2:
                    row1_str = " ".join(str(c) for c in table[1] if c).lower()
                    # Merge row 0 + row 1 as combined header
                    header_str = header_str + " " + row1_str

                is_relevant_table = any(kw in header_str for kw in TABLE_HEADER_KEYWORDS)

                if is_relevant_table:
                    has_table = True
                    tables_text += f"\nTable {j} (structured):\n"
                    tables_text += json.dumps(table, ensure_ascii=False) + "\n"


            # Decision: include page?
            # Strong keyword → always include
            # Weak keyword → only if page has a relevant table
            if has_strong or (has_weak and has_table):
                found_keywords = [kw for kw in STRONG + WEAK if kw in lower]
                relevant.append({
                    "page": i + 1,
                    "total_pages": total,
                    "text": text,
                    "tables_text": tables_text,
                    "has_table": has_table,
                    "keywords": found_keywords,
                })


    return relevant


def _try_camelot_tables(pdf_path: str, page_numbers: list[int]) -> dict[int, str]:
    """Try camelot on specific pages for better table extraction.

    Returns {page_number: csv_text} for pages where camelot succeeds.
    """
    camelot_results = {}
    try:
        import camelot
        for pg in page_numbers:
            try:
                tables = camelot.read_pdf(
                    pdf_path, flavor="lattice",
                    pages=str(pg), strip_text="\n"
                )
                if tables and len(tables) > 0:
                    csv_parts = []
                    for t in tables:
                        if not t.df.empty and len(t.df) > 1:
                            csv_parts.append(t.df.to_csv(index=False))
                    if csv_parts:
                        camelot_results[pg] = "\n".join(csv_parts)
            except Exception:
                pass  # camelot can fail on some pages
    except ImportError:
        pass  # camelot not installed

    return camelot_results


def _build_page_context(page_data: dict, camelot_csv: str | None = None) -> str:
    """Build the text context for one page to send to LLM."""
    parts = [f"--- Page {page_data['page']} of {page_data['total_pages']} ---"]
    parts.append(page_data["text"])

    if camelot_csv:
        parts.append(f"\n[Camelot CSV data for page {page_data['page']}]:")
        parts.append(camelot_csv)
    elif page_data["tables_text"]:
        parts.append(page_data["tables_text"])

    return "\n".join(parts)


# ── Chunking ─────────────────────────────────────────────────────────


def _chunk_pages(page_contexts: list[str], max_chars: int = 6000) -> list[str]:
    """Group page contexts into chunks that fit within LLM token limits."""
    if not page_contexts:
        return []

    chunks = []
    current_parts = []
    current_len = 0

    for ctx in page_contexts:
        ctx_len = len(ctx)
        if current_len + ctx_len > max_chars and current_parts:
            chunks.append("\n\n".join(current_parts))
            current_parts = []
            current_len = 0

        current_parts.append(ctx)
        current_len += ctx_len

    if current_parts:
        chunks.append("\n\n".join(current_parts))

    return chunks


# ── LLM Call ─────────────────────────────────────────────────────────


def _extract_chunk(
    agent: Agent, chunk_text: str,
    chunk_idx: int, total_chunks: int,
    meeting_name: str, last_scheme: str = "",
) -> list[NCTElement]:
    """Send one chunk to LLM with retries."""
    header = f"MEETING: {meeting_name} | CHUNK {chunk_idx}/{total_chunks}"
    if last_scheme:
        header += f"\nCONTEXT: Previous scheme was '{last_scheme}'. If first rows have no heading, they may continue this scheme."

    user_msg = f"{header}\n\n{chunk_text}"

    max_retries = 3
    for attempt in range(1, max_retries + 1):
        try:
            result = agent.run_sync(user_msg)
            return result.output
        except Exception as e:
            err = str(e).lower()
            retryable = any(x in err for x in ["timeout", "connection", "429", "503", "retry", "403"])
            if retryable and attempt < max_retries:
                wait = 2 ** attempt * 5
                print(f"    [RETRY] attempt {attempt}, wait {wait}s: {str(e)[:100]}")
                time.sleep(wait)
                continue
            raise


# ── Main Extraction ──────────────────────────────────────────────────


def extract_from_pdf(pdf_path: str) -> NCTExtractionResult:
    """Extract transmission scheme data from a single NCT PDF.

    1. Scan each page for scope keywords
    2. Only keyword-matched pages go to LLM
    3. Use camelot for pages with bordered tables
    4. Chunk and send to LLM
    5. Post-process: inherit scheme names, set source
    """
    agent = _get_agent()
    filename = os.path.basename(pdf_path)

    # Derive meeting name
    meeting_name = filename.replace(".pdf", "").replace("_", " ").strip()
    m = re.search(r"(\d+)(?:st|nd|rd|th)", meeting_name, re.IGNORECASE)
    if m:
        meeting_name = f"{m.group(0)} NCT Meeting"

    print(f"\n[nct] Processing: {filename}")

    # Step 1: Find pages with scope keywords
    relevant_pages = _extract_relevant_pages(pdf_path)

    if not relevant_pages:
        print(f"[nct]   No scope keywords found in any page. Skipping.")
        return NCTExtractionResult(meeting_name=meeting_name, elements=[])

    print(f"[nct]   {len(relevant_pages)} pages matched keywords (out of {relevant_pages[0]['total_pages']} total)")

    # Step 2: Try camelot on pages that have tables
    table_pages = [p["page"] for p in relevant_pages if p["has_table"]]
    camelot_data = {}
    if table_pages:
        camelot_data = _try_camelot_tables(pdf_path, table_pages)
        if camelot_data:
            print(f"[nct]   Camelot extracted tables from {len(camelot_data)} pages")

    # Step 3: Build page contexts
    page_contexts = []
    for p in relevant_pages:
        ctx = _build_page_context(p, camelot_data.get(p["page"]))
        page_contexts.append(ctx)

    # Step 4: Chunk
    chunks = _chunk_pages(page_contexts, max_chars=6000)
    total_chars = sum(len(c) for c in chunks)
    print(f"[nct]   {len(chunks)} chunk(s), {total_chars:,} chars")

    # Step 5: Extract from each chunk
    all_elements: list[NCTElement] = []
    last_scheme = ""
    t0 = time.time()

    for i, chunk in enumerate(chunks, 1):
        if not chunk.strip():
            continue
        print(f"[nct]   [{i}/{len(chunks)}] ({len(chunk):,} chars) ...")

        try:
            elems = _extract_chunk(agent, chunk, i, len(chunks), meeting_name, last_scheme)
            all_elements.extend(elems)

            # Track last scheme for cross-chunk continuity
            for elem in reversed(elems):
                if elem.scheme_name and elem.scheme_name.strip():
                    last_scheme = elem.scheme_name.strip()
                    break

            print(f"[nct]   [{i}/{len(chunks)}] -> {len(elems)} elements")
        except Exception as e:
            print(f"[nct]   [{i}/{len(chunks)}] ERROR: {e}")

    elapsed = time.time() - t0

    # Step 6: Post-process
    _post_process(all_elements, meeting_name)

    print(f"[nct]   DONE: {len(all_elements)} elements in {elapsed:.1f}s")
    return NCTExtractionResult(meeting_name=meeting_name, elements=all_elements)


def _post_process(elements: list[NCTElement], meeting_name: str):
    """Inherit scheme names forward, set source."""
    last_scheme = ""
    for elem in elements:
        if elem.scheme_name and elem.scheme_name.strip():
            last_scheme = elem.scheme_name.strip()
        elif last_scheme:
            elem.scheme_name = last_scheme
        if not elem.source:
            elem.source = meeting_name
