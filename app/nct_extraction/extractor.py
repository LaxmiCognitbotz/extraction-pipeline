"""NCT PDF → structured JSON extraction.

Extracts data page by page using pdfplumber for paragraphs and camelot for tabular structures.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path
from typing import List, Optional
import shutil

# Patch shutil.rmtree to suppress PermissionError from camelot temporary file cleanups at exit
_original_rmtree = shutil.rmtree
def _safe_rmtree(*args, **kwargs):
    try:
        _original_rmtree(*args, **kwargs)
    except PermissionError:
        pass
shutil.rmtree = _safe_rmtree

import pdfplumber
from pydantic_ai import Agent
from pydantic_ai.settings import ModelSettings

from app.nct_extraction.schemas import NCTElement, NCTExtractionResult
from app.nct_extraction.seed_extractor import load_registry, get_seeds_for_meeting

# ── Lazy agent init ──────────────────────────────────────────────────

_agent: Agent | None = None


def _get_agent() -> Agent[None, list[NCTElement]]:
    """Create the Pydantic AI agent using whatever LLM provider is configured."""
    global _agent
    if _agent is not None:
        return _agent

    from shared.llm import get_model, ensure_api_key
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
You are a highly capable data extraction agent for CEA NCT (National Committee on Transmission) meeting minutes.

You will receive chunks of page text plus tabular data from the meeting minutes.
The document has many variations in how schemes and scopes are presented. You must carefully analyze the text, paragraphs, and tables on each page to maintain context.

EXTRACT every transmission element/scope from the document. Each row in a "Scope of Works" or "Scope of the Transmission Scheme" table should be extracted as an NCTElement.

CRITICAL RULES FOR VARIATIONS AND CONTEXT:
1. IDENTIFYING SCHEME NAMES:
   - The `scheme_name` is usually the bold/numbered heading preceding the tables (e.g., "1.2. ERES-47: Nawada – Durgapur – Jeerat (New) 765kV corridor" or "Name of Scheme: ...").
   - If a scope table begins on a page without a clear heading, look at the CONTEXT provided at the top of your prompt (the "Previous scheme"). It is highly likely the table is a continuation of that previous scheme.
   - Apply the SAME `scheme_name` to ALL scope rows that fall under it. Keep the physical `scope` description SEPARATE from the `scheme_name`.
   - IMPORTANT: If KNOWN SCHEME NAMES are provided at the start of the user message, you MUST prefer those exact names. Match the text in the document to the closest known scheme name and use it verbatim. This avoids hallucination.

2. HANDLING SPLIT TABLES:
   - Very often, the details are split across two tables.
   - Table 1 might contain "Name of the scheme and tentative implementation timeframe", "Estimated Cost", and "Remarks" (which includes BPC/mode).
   - Table 2 (sometimes on the next page) contains the "Detailed scope" or "Scope of works".
   - You MUST combine the data from Table 1 (cost, timeframe, BPC) with the elements in Table 2 (scope, capacity) into the SAME extracted NCTElement objects for that scheme.

3. REVISED SCOPES:
   - If a table compares "Scope of works agreed in..." vs "Revised scope of works proposed", ALWAYS extract the "Revised" or "Proposed" scope, capacity, and cost.

4. FIELD EXTRACTION RULES:
   - `scope`: The physical description (lines, substations, reactors). Each separate row/item is one NCTElement. If a table continues on the next page, extract those rows as well.
   - `capacity_mva`: Compute the total. "3x1500MVA" = 4500.0. "2x500 MVA" = 1000.0. Only for substations/ICTs. Leave null for lines.
   - `length_km`: From "Length", "Lenth", "Line length", "Capacity/km", or "CKM" columns. Only for transmission lines. Leave null for substations/ICTs. Examples: "250 km" → 250.0, "320 CKM" → 320.0.
   - `project_cost_text`: Preserve EXACTLY as written (e.g., "5270.09", "339.11", or "Estimated Cost (₹ Crore)").
   - `execution_timeline`: From "Timeline" / "Timeframe" / "Schedule" column or paragraph (e.g., "31-03-2029", "36 months").
   - `tender_issuing_authority`: From "Implementing Agency", "BPC", or "Remarks" (e.g., "RECPDCL", "PFCCL").
   - `implementation_mode`: "TBCB" or "RTM" if stated in the table or paragraph.
   - `element_code`: The Sl.No. / Sr.No. from the scope table.

5. GENERAL & PARAGRAPH SCHEMES:
   - Not all schemes are in tables! Some transmission schemes, including their scope, capacity, cost, and timelines, are described entirely within paragraphs.
   - You MUST extract schemes described in paragraphs just as you would from a table.
   - Do NOT hallucinate. Extract only what is present.
   - Do NOT extract attendee lists, meeting procedures, or non-transmission-scope tables.
   - Leave `source` empty.
"""


# ── Page Extraction ──────────────────────────────────────────────────

def _extract_all_pages(pdf_path: str) -> list[dict]:
    """Extract all pages. Use pdfplumber for text, camelot for tables."""
    page_infos: list[dict] = []
    
    # Extract text using pdfplumber
    with pdfplumber.open(pdf_path) as pdf:
        total = len(pdf.pages)
        for i, page in enumerate(pdf.pages):
            text = page.extract_text() or ""
            page_infos.append({
                "page": i + 1,
                "total_pages": total,
                "text": text,
                "tables_text": "",
            })
            
    # Try extracting tables using camelot
    try:
        import camelot
        import logging
        # Suppress camelot logger output to avoid noise
        logging.getLogger("camelot").setLevel(logging.ERROR)
        
        for info in page_infos:
            pg = info["page"]
            try:
                # Lattice flavor detects table grids
                tables = camelot.read_pdf(
                    pdf_path, flavor="lattice",
                    pages=str(pg), strip_text="\n"
                )
                if tables and len(tables) > 0:
                    csv_parts = []
                    for i, t in enumerate(tables, 1):
                        if not t.df.empty and len(t.df) > 1:
                            csv_parts.append(f"Table P{pg}-T{i}:\n" + t.df.to_csv(index=False))
                    if csv_parts:
                        info["tables_text"] = "\n\n".join(csv_parts)
            except Exception as e:
                pass  # Ignore camelot errors on individual pages
    except ImportError:
        pass
        
    return page_infos


def _build_page_context(page_data: dict) -> str:
    """Build the text context for one page to send to LLM."""
    parts = [f"--- Page {page_data['page']} of {page_data['total_pages']} ---"]
    if page_data["text"]:
        parts.append("[Paragraphs/Text]:")
        parts.append(page_data["text"])

    if page_data["tables_text"]:
        parts.append("\n[Tabular Data]:")
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
    meeting_name: str,
    previous_elements: list[NCTElement] = None,
    known_scheme_names: list[str] = None,
) -> list[NCTElement]:
    """Send one chunk to LLM with retries."""
    header = f"MEETING: {meeting_name} | CHUNK {chunk_idx}/{total_chunks}"

    # ── Inject known (seeded) scheme names as ground-truth anchors ──
    seeds_str = ""
    if known_scheme_names:
        seeds_str = (
            "\nKNOWN SCHEME NAMES for this meeting (harvested from seed registry):\n"
        )
        for idx, name in enumerate(known_scheme_names, 1):
            seeds_str += f"  {idx}. {name}\n"
        seeds_str += (
            "IMPORTANT: When you find a scheme in the text, map it to the closest known scheme name above.\n"
            "The text might have variations (e.g., missing 'Transmission' at the start, or different Part A/Part B formats).\n"
            "Use FUZZY/SEMANTIC MATCHING. If it's clearly the same scheme, use the EXACT name from the list above.\n"
        )

    # ── Previous extraction context for continuations ──
    context_str = ""
    if previous_elements:
        context_str = "\nCONTEXT - Previously extracted elements from the previous page(s):\n"
        for el in previous_elements[-5:]:
            context_str += f"- Scheme: '{el.scheme_name}' | Scope: '{el.scope}' | Cost: '{el.project_cost_text}'\n"
        context_str += "\nIf the table or text in the current chunk is a continuation, USE this context to fill in the `scheme_name` and other missing fields.\n"

    user_msg = f"{header}{seeds_str}\n{context_str}\n{chunk_text}"

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

def extract_from_pdf(
    pdf_path: str,
    seed_registry: dict[str, list[str]] | None = None,
) -> NCTExtractionResult:
    """Extract transmission scheme data from a single NCT PDF.

    1. Extract every page (pdfplumber for text, camelot for tables).
    2. Chunk and send to LLM.
    3. Post-process: inherit scheme names, set source.
    """
    agent = _get_agent()
    filename = os.path.basename(pdf_path)

    # Derive meeting name
    meeting_name = filename.replace(".pdf", "").replace("_", " ").strip()
    m = re.search(r"(\d+)(?:st|nd|rd|th)", meeting_name, re.IGNORECASE)
    if m:
        meeting_name = f"{m.group(0)} NCT Meeting"

    # ── Load seed registry for grounding ──
    if seed_registry is None:
        seed_registry = load_registry()
    known_scheme_names = get_seeds_for_meeting(meeting_name, seed_registry)
    if known_scheme_names:
        print(f"[nct]   Seeding with {len(known_scheme_names)} known scheme names from later NCT minutes")
    else:
        print(f"[nct]   No seed scheme names found for {meeting_name} (will extract without anchors)")

    print(f"\n[nct] Processing: {filename}")

    # Step 1: Extract all pages
    page_infos = _extract_all_pages(pdf_path)

    if not page_infos:
        print(f"[nct]   No pages could be extracted. Skipping.")
        return NCTExtractionResult(meeting_name=meeting_name, source_pdf=filename, elements=[])

    print(f"[nct]   Extracting data page-by-page from {len(page_infos)} total pages")

    # Step 2: Build page contexts
    page_contexts = []
    for p in page_infos:
        ctx = _build_page_context(p)
        page_contexts.append(ctx)

    # Step 3: Chunk
    chunks = _chunk_pages(page_contexts, max_chars=8000)
    total_chars = sum(len(c) for c in chunks)
    print(f"[nct]   {len(chunks)} chunk(s), {total_chars:,} chars")

    # Step 4: Extract from each chunk
    all_elements: list[NCTElement] = []
    t0 = time.time()

    for i, chunk in enumerate(chunks, 1):
        if not chunk.strip():
            continue
        print(f"[nct]   [{i}/{len(chunks)}] ({len(chunk):,} chars) ...")

        try:
            elems = _extract_chunk(
                agent, chunk, i, len(chunks), meeting_name,
                previous_elements=all_elements,
                known_scheme_names=known_scheme_names,
            )
            all_elements.extend(elems)

            print(f"[nct]   [{i}/{len(chunks)}] -> {len(elems)} elements")
        except Exception as e:
            print(f"[nct]   [{i}/{len(chunks)}] ERROR: {e}")

    elapsed = time.time() - t0

    # Step 5: Post-process
    _post_process(all_elements, meeting_name)

    print(f"[nct]   DONE: {len(all_elements)} elements in {elapsed:.1f}s")
    return NCTExtractionResult(meeting_name=meeting_name, source_pdf=filename, elements=all_elements)


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
