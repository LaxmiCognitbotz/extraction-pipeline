"""Seed Extractor — backward-chaining scheme name harvester.

For every NCT PDF, each meeting contains a section like:
  "Status of the transmission schemes noted/approved/recommended to MoP in the 38th & 39th meeting of NCT"

This section contains CONFIRMED scheme names from PREVIOUS meetings.
We harvest them here to use as ground-truth anchors when extracting those older PDFs.

Approach:
  1. Read each PDF's "Status of previous schemes" table(s) using pdfplumber.
  2. Parse scheme names + the meeting number they belong to.
  3. Store in a JSON seed registry: { "38th NCT Meeting": ["Scheme A", "Scheme B", ...], ... }
  4. The main extractor reads from this registry and feeds known scheme names to the LLM.
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path
from typing import List, Optional

import pdfplumber
from pydantic import BaseModel, Field

# Suppress camelot temp-file PermissionError on Windows at exit
import shutil as _shutil
_orig_rmtree = _shutil.rmtree
def _safe_rmtree(*args, **kwargs):
    try:
        _orig_rmtree(*args, **kwargs)
    except PermissionError:
        pass
_shutil.rmtree = _safe_rmtree


# ── Registry path ──────────────────────────────────────────────────────
_DEFAULT_REGISTRY_PATH = Path(__file__).parent / "output" / "scheme_seed_registry.json"


# ── Pydantic schema for LLM seed extraction ───────────────────────────

class SeedMeetingGroup(BaseModel):
    """One group of scheme names belonging to a specific previous NCT meeting."""

    meeting_label: str = Field(
        description=(
            "The meeting this group of schemes was approved/recommended in. "
            "Format: '38th NCT Meeting', '39th NCT Meeting', etc. "
            "Extract the number from the section heading like "
            "'Status of schemes...in the 38th meeting of NCT'."
        )
    )
    scheme_names: List[str] = Field(
        description=(
            "List of transmission scheme names found in the 'Name of the Transmission Scheme' "
            "column of the status table for this meeting. "
            "Each name should be a full, clean scheme name like: "
            "'Transmission Network Expansion Scheme in Western Region to cater to pumped storage "
            "potential near Satara (up to 4500 MW): Part A'. "
            "Do NOT include: Sr.No., BPC names, Gazette info, Recommended/Approved text, dates. "
            "Do include the full multi-line scheme names joined into one string. "
            "If a cell spans two rows (Part A and Part B), return them as SEPARATE entries."
        )
    )


# ── Lazy LLM agent for seed extraction ───────────────────────────────

_seed_agent = None


def _get_seed_agent():
    """Lazy-init a Pydantic AI agent for seed extraction (separate from main extractor)."""
    global _seed_agent
    if _seed_agent is not None:
        return _seed_agent

    project_root = str(Path(__file__).parent.parent)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    try:
        from pydantic_ai import Agent
        from pydantic_ai.settings import ModelSettings
        from app.llm import get_model, ensure_api_key
        ensure_api_key()
        model = get_model()

        _seed_agent = Agent(
            model=model,
            output_type=list[SeedMeetingGroup],
            system_prompt=_SEED_SYSTEM_PROMPT,
            retries=2,
            model_settings=ModelSettings(
                temperature=0.0,
                max_tokens=4096,
                timeout=120,
            ),
        )
    except Exception as e:
        print(f"[seed-llm] Could not init LLM agent: {e}")
        _seed_agent = None

    return _seed_agent


_SEED_SYSTEM_PROMPT = """\
You are a precise data extraction agent for CEA NCT (National Committee on Transmission) meeting minutes.

Your ONLY task: extract transmission scheme names from sections describing the status of schemes
approved or recommended in PREVIOUS NCT meetings (not the current meeting being documented).

THESE SECTIONS CAN APPEAR IN TWO FORMATS:

FORMAT 1 — TABLE (most common in newer PDFs):
  Heading: "2 Status of the transmission schemes noted/approved/recommended to MoP in the 38th & 39th meeting of NCT"
  Sub-heading: "2.1 Status of new transmission schemes approved/recommended:"
  Table columns: Sr.No. | Name of the Transmission Scheme | Noted/Recommended/Approved | Mode | BPC | Gazette notification
  The meeting group may appear as a row inside the table (e.g. "22nd NCT Meeting" as a separator row).

FORMAT 2 — PARAGRAPH (common in older PDFs, no separate table):
  Text like:
    "The NCT in its 22nd meeting held on ... approved the following scheme: <Scheme Name>"
    "Status of new transmission schemes approved/recommended in 22nd NCT meeting:"
    "Following schemes were noted/approved in the 21st meeting of NCT:"
    "1. <scheme name>  2. <scheme name>  ..."
  In this format, scheme names appear as numbered/bulleted lists or inline text.

Your output must be a list of SeedMeetingGroup objects, one per unique PREVIOUS meeting referenced.

RULES:
1. MEETING LABEL: Extract the meeting number from context.
   - "38th meeting of NCT" → "38th NCT Meeting"
   - "22nd NCT Meeting" (as table group row) → "22nd NCT Meeting"
2. SCHEME NAMES — from TABLE format:
   - Extract from the "Name of the Transmission Scheme" column
   - Join multi-line cells into one string (but keep Part A and Part B as SEPARATE entries)
   - Do NOT include: BPC names (RECPDCL, PFCCL etc.), gazette info, status words, dates
   - Clean OCR artifacts: 'TTransmission' → 'Transmission'
3. SCHEME NAMES — from PARAGRAPH format:
   - Extract names from numbered lists or inline text following the approval/recommendation sentence
   - Include the FULL scheme name as written
   - A scheme name typically starts with 'Transmission', 'Augmentation', 'Eastern/Western/Northern Region',
     'ERES-', 'WRES-', 'NERES-', 'NERGS-', 'Network Expansion', 'System for', 'Scheme for', etc.
   - CRITICAL: Do NOT extract sub-items (like i, ii, iii) if they are specific transmission lines, 
     substations, or ICTs (e.g., "Establishment of...", "Ramgarh PS - Fatehgarh PS...", "1x500 MVA..."). 
     These are SCOPE items of a larger scheme, NOT schemes themselves. IGNORE THEM.
4. SCOPE COLUMN: Do NOT extract 'scope' or 'elements' — only the scheme NAME.
5. IGNORE: attendees, meeting procedures, scope-of-works for NEW schemes in the CURRENT meeting.
6. Do NOT hallucinate. Only extract what is explicitly written in the text.
7. If content continues on next page, extract from both pages.
"""


_CURRENT_MEETING_SYSTEM_PROMPT = """\
You are a precise data extraction agent for CEA NCT (National Committee on Transmission) meeting minutes.

Your task: extract the names of transmission schemes being DISCUSSED, APPROVED, or RECOMMENDED
for the FIRST TIME in the CURRENT NCT meeting (not from previous meetings' status sections).

These schemes appear in sections like:
  "4 New Transmission Schemes"
  "4.1 Transmission scheme for..."
  "3 New Transmission Schemes"
  "Agenda item: Recommendation of the following scheme:"
  Tables with columns: "Name of Scheme / Implementing Agency / Estimated Cost / Date"
  Tables: "Sl.No. | Name of the scheme and tentative implementation timeframe | Estimated Cost | Remarks"

Your output must be a list of SeedMeetingGroup objects.
Use meeting_label = the CURRENT meeting (e.g., "40th NCT Meeting").

RULES:
1. MEETING LABEL: Use the current meeting number provided in the prompt header.
2. SCHEME NAMES:
   - Extract the FULL scheme name from sub-section headings ("4.1 Scheme name..."),
     table "Name of Scheme" columns, or paragraph headings
   - Include Part A / Part B etc. as SEPARATE entries if they are separate scopes
   - The heading format: "4.1 Transmission scheme for evacuation of power..." — the scheme name
     is the text after the number: "Transmission scheme for evacuation of power..."
   - Join multi-line names into a single clean string
   - CRITICAL: Do NOT extract sub-scope items! If an item is a specific transmission line (e.g., "Khetri - Narela 765 kV D/c"), 
     a substation ("Establishment of..."), a reactor ("1x80 MVAr..."), or an ICT ("1x500 MVA..."), IT IS A SCOPE ITEM, NOT A SCHEME. IGNORE IT.
3. Do NOT extract schemes from the 'Status of previous meetings' section.
4. Do NOT hallucinate. Extract only what is explicitly written.
5. NORMALISE the name: strip leading/trailing whitespace, join broken lines.
"""


# ── Meeting number utilities ───────────────────────────────────────────

def _ordinal_to_int(ordinal: str) -> Optional[int]:
    """Convert '38th', '39th', '40th', etc. to int."""
    m = re.match(r"(\d+)", ordinal.strip())
    return int(m.group(1)) if m else None


def _int_to_ordinal_label(n: int) -> str:
    """Convert 38 → '38th NCT Meeting' style label."""
    if 11 <= (n % 100) <= 13:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix} NCT Meeting"


def _pdf_meeting_number(filename: str) -> Optional[int]:
    """Extract meeting number from filename like '01_40th_NCT_MoM.pdf'."""
    m = re.search(r"(\d+)(?:st|nd|rd|th)_NCT", filename, re.IGNORECASE)
    if m:
        return int(m.group(1))
    return None


# ── Text-based status section parser ──────────────────────────────────

# Matches lines like:
#   "2 Status of the transmission schemes noted/approved/recommended to MoP in the 38th"
#   "& 39th meeting of NCT:"
#   "2.1 Status of the transmission schemes noted/approved/recommended to MoP in the"
#   "38th meeting of NCT"
_STATUS_HEADING_RE = re.compile(
    r"status\s+of\s+the\s+transmission\s+schemes?",
    re.IGNORECASE,
)

# Ordinal pattern
_ORDINAL_RE = re.compile(r"\b(\d+(?:st|nd|rd|th))\b", re.IGNORECASE)

_SCHEME_LINE_SKIP_RE = re.compile(
    r"^(sr\.?\s*no|s\.?\s*no|sl\.?\s*no|name\s+of|noted|recommended|approved|mode|bpc|gazette|implementation|status|modification|scheme\s+where|proposed|$)",
    re.IGNORECASE,
)


def _looks_like_scheme_name(text: str) -> bool:
    """Heuristic: does this text look like a transmission scheme name?"""
    t = text.strip()
    if len(t) < 15:
        return False
    if _SCHEME_LINE_SKIP_RE.match(t):
        return False
    # Must contain at least some alphabetical content
    if not re.search(r"[A-Za-z]{4,}", t):
        return False
    return True


def _extract_status_section_text(pdf_path: str) -> list[tuple[list[int], str]]:
    """
    Return a list of (meeting_numbers, block_text) tuples.
    Each tuple represents one 'Status of schemes in Nth meeting' section.

    Handles the real-world case where:
    - The section heading may be split across two lines
    - The meeting ordinal may appear on the same line or next line
    """
    sections: list[tuple[list[int], str]] = []

    with pdfplumber.open(pdf_path) as pdf:
        full_text_pages: list[tuple[int, str]] = []
        for i, page in enumerate(pdf.pages):
            txt = page.extract_text() or ""
            full_text_pages.append((i + 1, txt))

    # Flatten to lines with page markers
    all_lines: list[str] = []
    for pg, txt in full_text_pages:
        all_lines.append(f"__PAGE_{pg}__")
        all_lines.extend(txt.splitlines())

    # Find status-section headings
    i = 0
    while i < len(all_lines):
        line = all_lines[i]
        if _STATUS_HEADING_RE.search(line):
            # Collect meeting numbers from this line + next 2 lines
            # (heading may be split across lines)
            lookahead = " ".join(all_lines[i:i+3])
            
            # Filter: must reference "meeting of NCT" in the lookahead
            if not re.search(r"meeting\s+of\s+NCT", lookahead, re.IGNORECASE):
                i += 1
                continue

            # Filter: skip "modification" sub-sections — we only want scheme name tables
            if re.search(r"modification", lookahead, re.IGNORECASE):
                i += 1
                continue

            ordinals_found = _ORDINAL_RE.findall(lookahead)
            meeting_nums: list[int] = []
            for ord_str in ordinals_found:
                n = _ordinal_to_int(ord_str)
                if n and n < 100:  # Sanity: meeting numbers won't exceed 100
                    meeting_nums.append(n)

            if meeting_nums:
                # Collect the next ~100 lines as the block for this section
                block_lines = [line]
                j = i + 1
                while j < min(i + 100, len(all_lines)):
                    next_line = all_lines[j]
                    # Stop if we hit a new major section (e.g. "3 Modifications...")
                    if re.match(r"^\d+\s+[A-Z][a-z]", next_line) and j > i + 5:
                        break
                    # Stop if we hit another numbered sub-section heading with "status"
                    if re.match(r"^\d+\.\d+\s+Status\s+of", next_line, re.IGNORECASE) and j > i + 3:
                        break
                    block_lines.append(next_line)
                    j += 1

                sections.append((meeting_nums, "\n".join(block_lines)))
                i = j  # Skip ahead past this block
                continue

        i += 1

    return sections


def _split_merged_scheme_names(raw: str) -> list[str]:
    """
    Some PDF cells have two scheme names merged together due to OCR/text extraction.
    Example: 'TTransmission Network...Part Aransmission Network...Part B'
    This happens when Part A and Part B rows are merged: 'Part A' + 'ransmission...' = 'Part Aransmission'

    Strategy: find 'Part X<lowercase>' patterns that indicate the merge point.
    """
    # Fix leading double characters (TTransmission -> Transmission)
    raw = re.sub(r"^T(Transmission)", r"\1", raw)
    raw = re.sub(r"^R(Recommended)", r"\1", raw)

    # Pattern: 'Part A' immediately followed by lowercase (the start of next scheme's word)
    # e.g. 'Part Aransmission' = 'Part A' + 'ransmission...'
    # Split on: (Part [A-Z])([a-z]) where the capital letter is a Part designator
    # and the lowercase starts the next scheme name
    m = re.search(r"(Part\s+[A-Z])([a-z])", raw)
    if m:
        split_pos = m.start(2)  # position of the lowercase char
        part1 = raw[:split_pos].strip()
        remainder = raw[split_pos:]

        # The remainder starts with a lowercase letter that is the 2nd letter of the next scheme's first word
        # Most common: 'ransmission' -> 'Transmission', 'vacuation' -> 'Evacuation', 'ransmission' -> 'Transmission'
        # Try known prefixes
        _KNOWN_SCHEME_STARTERS = [
            ("ransmission", "Transmission"),
            ("vacuation", "Evacuation"),
            ("ERES", "ERES"),
            ("transmission", "Transmission"),
        ]
        part2 = None
        for suffix, full_word in _KNOWN_SCHEME_STARTERS:
            if remainder.startswith(suffix):
                part2 = full_word + remainder[len(suffix):]
                break

        if part2 is None:
            # Generic: capitalize the first letter
            part2 = remainder[0].upper() + remainder[1:]

        # Recurse in case Part B also merged with Part C etc.
        part2_parts = _split_merged_scheme_names(part2)
        return [part1] + part2_parts

    return [raw.strip()]


def _clean_scheme_name(raw: str) -> str:
    """Clean a raw cell value into a proper scheme name."""
    # Remove leading sr.no prefixes like "1.", "2.", "A."
    raw = re.sub(r"^\d+\.\s*", "", raw).strip()
    # Fix known OCR artifacts: TTransmission -> Transmission
    raw = re.sub(r"^T(Transmission)", r"\1", raw)
    # Collapse internal whitespace
    raw = re.sub(r"\s+", " ", raw).strip()
    # Remove trailing continuation markers like just '–' or '—'
    raw = re.sub(r"[\u2013\u2014\-]+\s*$", "", raw).strip()
    return raw


def _is_status_page(page_text: str) -> bool:
    """Check if a page contains a 'Status of schemes' heading for previous meetings."""
    return bool(
        _STATUS_HEADING_RE.search(page_text)
        and re.search(r"meeting\s+of\s+NCT", page_text, re.IGNORECASE)
        and not re.search(r"modification", page_text[:500], re.IGNORECASE)
    )


def _find_status_pages(pdf_path: str) -> list[tuple[int, list[int]]]:
    """
    Scan all pages, find those with 'Status of schemes from Nth meeting' headings.
    Returns list of (page_number_1indexed, [meeting_nums_referenced]).

    Handles:
    - Standard: heading on one line with ordinal ("...in the 38th meeting of NCT")
    - Split: ordinal on next line ("...in the 22nd\nand 23rd meetings of NCT:")
    - Plural: "meetings of NCT" instead of "meeting of NCT"
    """
    results: list[tuple[int, list[int]]] = []
    seen_pages: set[int] = set()

    # Pattern: singular OR plural "meeting(s) of NCT"
    _NCT_MEETING_RE = re.compile(r"meetings?\s+of\s+NCT", re.IGNORECASE)

    with pdfplumber.open(pdf_path) as pdf:
        pages_text: list[tuple[int, str]] = []
        for i, page in enumerate(pdf.pages):
            txt = page.extract_text() or ""
            pages_text.append((i + 1, txt))

    for page_num, page_text in pages_text:
        if not _STATUS_HEADING_RE.search(page_text):
            continue

        lines = page_text.splitlines()
        for i, line in enumerate(lines):
            if not _STATUS_HEADING_RE.search(line):
                continue

            # ── Filter: must reference "meeting(s) of NCT" within next 5 lines ──
            lookahead_lines = lines[i:i+5]
            lookahead = " ".join(lookahead_lines)
            if not _NCT_MEETING_RE.search(lookahead):
                continue

            # ── Filter: skip if the heading LINE ITSELF says "modification" ──
            # (don't filter based on surrounding content — that's too aggressive)
            if re.search(r"modification", line, re.IGNORECASE):
                continue

            # ── Extract ordinals from the heading + next 2 lines ──
            heading_context = " ".join(lines[i:i+3])
            ordinals = _ORDINAL_RE.findall(heading_context)
            meeting_nums = []
            for ord_str in ordinals:
                n = _ordinal_to_int(ord_str)
                if n and n < 100:
                    meeting_nums.append(n)

            if meeting_nums and page_num not in seen_pages:
                results.append((page_num, meeting_nums))
                seen_pages.add(page_num)

    return results


def _find_status_pages_paragraph(pdf_path: str) -> list[tuple[int, list[int]]]:
    """
    Fallback: detect pages with paragraph-format status descriptions.

    Older NCT PDFs don't use a standard status table heading. Instead they have
    paragraphs like:
      "The NCT in its 22nd meeting held on ... approved the following scheme:"
      "Status of new transmission schemes approved/recommended in 22nd NCT meeting:"
      "The following schemes were approved in the 21st meeting of NCT:"

    Returns list of (page_number_1indexed, [meeting_nums_referenced]).
    """
    results: list[tuple[int, list[int]]] = []
    seen_pages: set[int] = set()

    _PARA_STATUS_RE = re.compile(
        r"(?:"
        r"NCT\s+in\s+its\s+\d+(?:st|nd|rd|th)\s+meeting"          # NCT in its 22nd meeting
        r"|status\s+of\s+(?:new\s+)?transmission\s+scheme"          # Status of new transmission scheme
        r"|following\s+scheme[s]?\s+(?:were|was)\s+approved"        # following schemes were approved
        r"|approved\s+(?:the\s+)?following\s+(?:transmission\s+)?scheme"  # approved the following scheme
        r"|scheme[s]?\s+noted\s*/\s*approved\s*/\s*recommended"     # schemes noted/approved/recommended
        r")",
        re.IGNORECASE,
    )

    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages):
            txt = page.extract_text() or ""
            if not txt.strip() or not _PARA_STATUS_RE.search(txt):
                continue

            # Extract ordinals from the whole page text
            ordinals = _ORDINAL_RE.findall(txt)
            meeting_nums = []
            for ord_str in ordinals:
                n = _ordinal_to_int(ord_str)
                if n and n < 100:
                    meeting_nums.append(n)

            if meeting_nums and (i + 1) not in seen_pages:
                results.append((i + 1, meeting_nums))
                seen_pages.add(i + 1)

    return results


def _extract_scheme_names_via_camelot(pdf_path: str, pages: list[int]) -> list[str]:
    """
    Use camelot on the specified pages to extract scheme names from
    'Name of the Transmission Scheme' columns in status tables.
    """
    scheme_names: list[str] = []

    try:
        import camelot
        import logging
        logging.getLogger("camelot").setLevel(logging.ERROR)
    except ImportError:
        return scheme_names

    page_str = ",".join(str(p) for p in pages)
    try:
        tables = camelot.read_pdf(pdf_path, flavor="lattice", pages=page_str, strip_text="\n")
    except Exception:
        return scheme_names

    for table in tables:
        df = table.df
        if df.empty or len(df) < 2:
            continue

        full_text_lower = " ".join(str(c) for c in df.values.flatten()).lower()
        # Only process status/scheme tables, not scope tables
        if "name of the transmission scheme" not in full_text_lower and "transmission scheme" not in full_text_lower:
            continue
        # Skip if it looks like a scope-of-works table
        if any(kw in full_text_lower for kw in ["scope of work", "capacity/km", "line length", "substation"]):
            continue
        # Must look like a status table
        if not any(kw in full_text_lower for kw in ["noted", "recommended", "approved", "gazette", "bpc"]):
            continue

        # Find scheme name column
        scheme_col_idx = None
        header_row_idx = 0

        for r_idx, row in df.iterrows():
            for c_idx, cell in enumerate(row):
                cell_str = str(cell).lower().strip()
                if "name" in cell_str and ("transmission scheme" in cell_str or "scheme" in cell_str):
                    scheme_col_idx = c_idx
                    header_row_idx = r_idx
                    break
            if scheme_col_idx is not None:
                break

        if scheme_col_idx is None:
            # Try column 1 (index 1) as default — column 0 is usually Sr.No
            scheme_col_idx = 1
            header_row_idx = 0

        # Extract scheme names from that column, skipping header row
        for r_idx, row in df.iterrows():
            if r_idx <= header_row_idx:
                continue
            try:
                cell_val = str(row.iloc[scheme_col_idx]).strip()
            except Exception:
                continue

            # Handle merged multi-scheme cells (Part A + Part B)
            candidates = _split_merged_scheme_names(cell_val)
            for candidate in candidates:
                cleaned = _clean_scheme_name(candidate)

                # Skip group-separator rows like "22nd NCT Meeting", "23rd NCT Meeting"
                if re.match(r"^\d+(?:st|nd|rd|th)\s+NCT\s+Meeting\s*$", cleaned, re.IGNORECASE):
                    continue

                # Skip cells that start with lowercase or special chars (continuation fragments)
                if cleaned and (cleaned[0].islower() or cleaned[0] in "(-,"):
                    continue

                if _looks_like_scheme_name(cleaned) and len(cleaned) > 15:
                    scheme_names.append(cleaned)

    return scheme_names


def _extract_scheme_names_heuristic(block_text: str) -> list[str]:
    """
    Fallback heuristic: extract scheme names from raw garbled pdfplumber text.
    Used when camelot fails or finds nothing.
    """
    scheme_names: list[str] = []
    lines = block_text.splitlines()
    current_scheme_lines: list[str] = []

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("__PAGE_"):
            if current_scheme_lines:
                combined = " ".join(current_scheme_lines).strip()
                combined = _clean_scheme_name(combined)
                if _looks_like_scheme_name(combined):
                    scheme_names.append(combined)
                current_scheme_lines = []
            continue

        if _SCHEME_LINE_SKIP_RE.match(stripped):
            if current_scheme_lines:
                combined = " ".join(current_scheme_lines).strip()
                combined = _clean_scheme_name(combined)
                if _looks_like_scheme_name(combined):
                    scheme_names.append(combined)
                current_scheme_lines = []
            continue

        # Skip obvious non-scheme-name content
        if re.search(r"(gazette|notification|MoP|TBCB|RTM|RECPDCL|PFCCL|PFFCL|Recommended|Approved|Noted)", stripped):
            if current_scheme_lines:
                combined = " ".join(current_scheme_lines).strip()
                combined = _clean_scheme_name(combined)
                if _looks_like_scheme_name(combined):
                    scheme_names.append(combined)
                current_scheme_lines = []
            continue

        if _looks_like_scheme_name(stripped):
            if current_scheme_lines and not re.match(r"^\d+\.", stripped):
                current_scheme_lines.append(stripped)
            else:
                if current_scheme_lines:
                    combined = " ".join(current_scheme_lines).strip()
                    combined = _clean_scheme_name(combined)
                    if _looks_like_scheme_name(combined):
                        scheme_names.append(combined)
                current_scheme_lines = [stripped]
        else:
            if current_scheme_lines:
                combined = " ".join(current_scheme_lines).strip()
                combined = _clean_scheme_name(combined)
                if _looks_like_scheme_name(combined):
                    scheme_names.append(combined)
            current_scheme_lines = []

    if current_scheme_lines:
        combined = " ".join(current_scheme_lines).strip()
        combined = _clean_scheme_name(combined)
        if _looks_like_scheme_name(combined):
            scheme_names.append(combined)

    return scheme_names


def _build_seed_llm_context(
    pdf_path: str,
    pages: list[int],
    paragraph_mode: bool = False,
) -> str:
    """
    Build a rich context string for the LLM seed agent:
    - Raw page text (pdfplumber)
    - Camelot CSV tables from the status pages
    Both are included so the LLM can cross-reference them.
    """
    parts: list[str] = []

    # Raw text
    with pdfplumber.open(pdf_path) as pdf:
        total = len(pdf.pages)
        for pg_num in pages:
            if pg_num < 1 or pg_num > total:
                continue
            page = pdf.pages[pg_num - 1]
            txt = page.extract_text() or ""
            if txt.strip():
                parts.append(f"--- Page {pg_num} [Raw Text] ---")
                parts.append(txt)

    if paragraph_mode:
        parts.insert(0, (
            "[MODE: PARAGRAPH] This PDF uses paragraph-format scheme descriptions, not a standard status table.\n"
            "Look for scheme names in numbered lists, inline text, and paragraph descriptions of\n"
            "previously approved/recommended/noted schemes from earlier NCT meetings."
        ))

    # Camelot tables (lattice for bordered tables, stream for borderless)
    try:
        import camelot
        import logging
        logging.getLogger("camelot").setLevel(logging.ERROR)

        page_str = ",".join(str(p) for p in pages)
        for flavor in ["lattice", "stream"]:
            try:
                tables = camelot.read_pdf(pdf_path, flavor=flavor, pages=page_str, strip_text="\n")
                for t in tables:
                    if t.df.empty:
                        continue
                    full = " ".join(str(c) for c in t.df.values.flatten()).lower()
                    # In paragraph_mode, be more inclusive; in table mode, filter to status tables only
                    if paragraph_mode or any(k in full for k in [
                        "name of the transmission", "noted", "recommended", "approved", "gazette", "bpc"
                    ]):
                        parts.append(f"--- Page {t.page} [Camelot {flavor.title()} Table CSV] ---")
                        parts.append(t.df.to_csv(index=False))
            except Exception:
                continue
    except Exception:
        pass

    return "\n\n".join(parts)


def _extract_seeds_via_llm(
    pdf_path: str,
    pages: list[int],
    current_meeting_num: Optional[int] = None,
    paragraph_mode: bool = False,
) -> dict[str, list[str]]:
    """
    Use the LLM to extract scheme names from the status-review section pages.
    Returns dict: meeting_label -> [scheme_name, ...]
    """
    seeds: dict[str, list[str]] = {}
    agent = _get_seed_agent()
    if agent is None:
        return seeds

    context = _build_seed_llm_context(pdf_path, pages, paragraph_mode=paragraph_mode)
    if not context.strip():
        return seeds

    header = "NCT MEETING MINUTES — STATUS SECTION EXTRACTION\n"
    if current_meeting_num:
        header += (
            f"This is the {_int_to_ordinal_label(current_meeting_num)} PDF. "
            f"Extract scheme names ONLY from sections describing schemes from PREVIOUS meetings.\n"
            f"Do NOT extract new schemes being discussed FOR the first time in "
            f"{_int_to_ordinal_label(current_meeting_num)}.\n"
        )
    if paragraph_mode:
        header += (
            "\n[IMPORTANT] This PDF uses PARAGRAPH FORMAT — scheme names appear in inline text or "
            "numbered lists, not in a standard status table. Look carefully for patterns like:\n"
            "  - Numbered lists: '1. Scheme name  2. Scheme name'\n"
            "  - Inline approvals: 'NCT approved ... scheme: <name>'\n"
            "  - Paragraph sentences describing scheme status\n"
        )
    header += "\nEXTRACT scheme names from the content below:\n"

    prompt = header + "\n" + context

    try:
        result = agent.run_sync(prompt)
        groups: list[SeedMeetingGroup] = result.output
        for grp in groups:
            label = grp.meeting_label.strip()
            # Normalise label format
            m = re.search(r"(\d+)", label)
            if m:
                n = int(m.group(1))
                label = _int_to_ordinal_label(n)
            if label not in seeds:
                seeds[label] = []
            for name in grp.scheme_names:
                name = _clean_scheme_name(name)
                if _looks_like_scheme_name(name) and name not in seeds[label]:
                    seeds[label].append(name)
    except Exception as e:
        print(f"[seed-llm] LLM extraction failed: {e}")

    return seeds


def _find_new_scheme_pages(pdf_path: str) -> list[int]:
    """
    Find pages containing 'New Transmission Schemes' sections.
    Typically numbered '4 New Transmission Schemes' or '3 New...'
    Returns list of 1-indexed page numbers.
    """
    results: set[int] = set()
    _NEW_SCHEME_RE = re.compile(
        r"^\s*(?:\d+\.?\s*)?New\s+Transmission\s+Scheme", re.IGNORECASE | re.MULTILINE
    )

    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages):
            txt = page.extract_text() or ""
            if _NEW_SCHEME_RE.search(txt):
                results.add(i + 1)
                # Usually spans a few pages, grab up to 5 pages to be safe
                for offset in range(1, 6):
                    if i + 1 + offset <= len(pdf.pages):
                        results.add(i + 1 + offset)

    return sorted(list(results))


def _extract_current_meeting_seeds_via_llm(
    pdf_path: str,
    pages: list[int],
    current_meeting_num: int,
) -> list[str]:
    """
    Use the LLM to extract scheme names discussed FOR THE FIRST TIME in this meeting.
    """
    agent = _get_seed_agent()
    if agent is None or not pages:
        return []

    context = _build_seed_llm_context(pdf_path, pages, paragraph_mode=True)
    if not context.strip():
        return []

    header = "NCT MEETING MINUTES — CURRENT MEETING NEW SCHEMES EXTRACTION\n"
    header += f"This is the {_int_to_ordinal_label(current_meeting_num)} PDF.\n"
    header += "EXTRACT new transmission scheme names from the text below:\n"

    prompt = header + "\n" + context

    try:
        # We temporarily override the system prompt for the agent
        # (pydantic-ai doesn't easily support per-call system prompts on the same Agent instance
        # without overriding, but we can create a quick temporary agent)
        from pydantic_ai import Agent
        from pydantic_ai.settings import ModelSettings
        from app.llm import get_model, ensure_api_key
        
        ensure_api_key()
        
        temp_agent = Agent(
            model=get_model(),
            output_type=list[SeedMeetingGroup],
            system_prompt=_CURRENT_MEETING_SYSTEM_PROMPT,
            retries=2,
            model_settings=ModelSettings(temperature=0.0, max_tokens=4096, timeout=120),
        )
        result = temp_agent.run_sync(prompt)
        
        # We should only get one group back, labeled with current_meeting_num
        schemes = []
        for grp in result.output:
            for name in grp.scheme_names:
                name = _clean_scheme_name(name)
                if _looks_like_scheme_name(name):
                    schemes.append(name)
        return schemes

    except Exception as e:
        print(f"[seed-llm] Current meeting extraction failed: {e}")
        return []


def _merge_seed_dicts(*dicts: dict[str, list[str]]) -> dict[str, list[str]]:
    """Union-merge multiple seed dicts, preserving order and avoiding duplicates."""
    result: dict[str, list[str]] = {}
    for d in dicts:
        for label, names in d.items():
            if label not in result:
                result[label] = []
            for name in names:
                if name not in result[label]:
                    result[label].append(name)
    return result


def extract_seeds_from_pdf(
    pdf_path: str,
    use_llm: bool = True,
) -> dict[str, list[str]]:
    """
    Main function: scan one NCT PDF for status-review sections and return
    a dict mapping meeting_label -> list of scheme names.

    Three-pass approach (best accuracy through redundancy):
      Pass 1 — Camelot: structured table extraction, finds scheme name column
      Pass 2 — LLM:     understands context, recovers truncated/garbled names,
                         correctly maps names to meeting numbers per sub-section
      Pass 3 — Heuristic: regex/line-scan fallback if both above are empty

    Results from all passes are union-merged (no duplicates).
    """
    seeds: dict[str, list[str]] = {}

    # ── Stage 0: Identify status pages ──
    status_pages = _find_status_pages(pdf_path)

    # If no table-format status sections found, try paragraph-format fallback
    using_paragraph_mode = False
    if not status_pages:
        status_pages = _find_status_pages_paragraph(pdf_path)
        using_paragraph_mode = bool(status_pages)
        if using_paragraph_mode:
            print(f"[seed]   Paragraph-mode detection: {len(status_pages)} pages")

    if not status_pages:
        return seeds

    # Collect all relevant pages (include +1 for table continuation)
    all_status_page_nums: list[int] = []
    all_meeting_nums: list[int] = []

    for page_num, meeting_nums in status_pages:
        all_status_page_nums.append(page_num)
        all_status_page_nums.append(page_num + 1)  # span to next page
        all_meeting_nums.extend(meeting_nums)

    unique_pages = sorted(set(all_status_page_nums))
    unique_meeting_nums = list(set(all_meeting_nums))

    # Infer current meeting number from filename for LLM context
    current_meeting_num = _pdf_meeting_number(str(Path(pdf_path).name))

    # ── Pass 1: Camelot ──
    camelot_names = _extract_scheme_names_via_camelot(pdf_path, unique_pages)
    camelot_seeds: dict[str, list[str]] = {}
    if camelot_names:
        for mn in unique_meeting_nums:
            label = _int_to_ordinal_label(mn)
            camelot_seeds[label] = list(camelot_names)  # all names to all meetings (coarse)

    # ── Pass 2: LLM (more accurate meeting-level mapping) ──
    llm_seeds: dict[str, list[str]] = {}
    if use_llm:
        llm_seeds = _extract_seeds_via_llm(
            pdf_path, unique_pages, current_meeting_num,
            paragraph_mode=using_paragraph_mode,
        )

    # ── Pass 3: Heuristic fallback (if both above returned nothing) ──
    heuristic_seeds: dict[str, list[str]] = {}
    if not camelot_seeds and not llm_seeds:
        sections = _extract_status_section_text(pdf_path)
        for meeting_nums, block_text in sections:
            names = _extract_scheme_names_heuristic(block_text)
            for mn in meeting_nums:
                label = _int_to_ordinal_label(mn)
                if label not in heuristic_seeds:
                    heuristic_seeds[label] = []
                for name in names:
                    if name not in heuristic_seeds[label]:
                        heuristic_seeds[label].append(name)

    # ── Pass 4: Extract current meeting's NEW schemes ──
    current_meeting_seeds: dict[str, list[str]] = {}
    if use_llm and current_meeting_num:
        new_scheme_pages = _find_new_scheme_pages(pdf_path)
        if new_scheme_pages:
            current_names = _extract_current_meeting_seeds_via_llm(
                pdf_path, new_scheme_pages, current_meeting_num
            )
            if current_names:
                current_label = _int_to_ordinal_label(current_meeting_num)
                current_meeting_seeds[current_label] = current_names
                print(f"[seed]   Found {len(current_names)} new schemes for {current_label}")

    # ── Merge all passes ──
    # LLM is preferred (more accurate per-meeting mapping).
    # If the LLM successfully extracted schemes, we ONLY use camelot/heuristic 
    # for meetings that the LLM completely missed, to avoid polluting accurate mapping.
    if use_llm and llm_seeds:
        for label, names in camelot_seeds.items():
            if label not in llm_seeds:
                llm_seeds[label] = names
        for label, names in heuristic_seeds.items():
            if label not in llm_seeds:
                llm_seeds[label] = names
        seeds = _merge_seed_dicts(llm_seeds, current_meeting_seeds)
    else:
        seeds = _merge_seed_dicts(llm_seeds, camelot_seeds, heuristic_seeds, current_meeting_seeds)

    return seeds


# ── Registry management ────────────────────────────────────────────────

def load_registry(registry_path: Path = _DEFAULT_REGISTRY_PATH) -> dict[str, list[str]]:
    """Load seed registry from JSON. Returns empty dict if not found."""
    if registry_path.exists():
        with open(registry_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_registry(registry: dict[str, list[str]], registry_path: Path = _DEFAULT_REGISTRY_PATH):
    """Persist seed registry to JSON."""
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    with open(registry_path, "w", encoding="utf-8") as f:
        json.dump(registry, f, indent=2, ensure_ascii=False)


def merge_seeds_into_registry(
    new_seeds: dict[str, list[str]],
    registry: dict[str, list[str]],
) -> dict[str, list[str]]:
    """Merge newly found seeds into the existing registry (no duplicates)."""
    for label, names in new_seeds.items():
        if label not in registry:
            registry[label] = []
        for name in names:
            if name not in registry[label]:
                registry[label].append(name)
    return registry


def build_seed_registry(
    pdf_dir: str,
    registry_path: Path = _DEFAULT_REGISTRY_PATH,
    use_llm: bool = True,
    llm_rate_limit_sleep: float = 2.0,
) -> dict[str, list[str]]:
    """
    Scan ALL NCT PDFs and build the seed registry.

    Three-pass extraction per PDF (Camelot + LLM + Heuristic fallback).
    Process from newest to oldest: newer meetings reference older ones,
    so scanning new PDFs first gives us seeds for older PDFs.

    Args:
        pdf_dir: Directory containing NCT PDFs.
        registry_path: Where to save/load the JSON registry.
        use_llm: If True, run the LLM pass for richer extraction. Default True.
        llm_rate_limit_sleep: Seconds to sleep between PDFs when LLM is used.
    """
    registry = load_registry(registry_path)
    registry_by_pdf: dict[str, dict[str, list[str]]] = {}

    pdf_files = sorted(
        [f for f in Path(pdf_dir).iterdir() if f.suffix.lower() == ".pdf"],
        key=lambda p: _pdf_meeting_number(p.name) or 0,
        reverse=True,  # newest first
    )

    total = len(pdf_files)
    print(f"\n[seed] Building seed registry from {total} PDFs (LLM={'ON' if use_llm else 'OFF'})...")

    for i, pdf_path in enumerate(pdf_files, 1):
        print(f"[seed] [{i}/{total}] Scanning: {pdf_path.name}")
        try:
            seeds = extract_seeds_from_pdf(str(pdf_path), use_llm=use_llm)
            if seeds:
                total_names = sum(len(v) for v in seeds.values())
                meetings_found = sorted(seeds.keys())
                print(f"[seed]   ✓ {total_names} scheme names for meetings: {meetings_found}")
                for meeting, names in seeds.items():
                    for name in names:
                        print(f"[seed]     [{meeting}] {name[:90]}")
                registry = merge_seeds_into_registry(seeds, registry)
                
                # Keep a track of what came from which PDF for user verification
                pdf_name_str = pdf_path.name
                if pdf_name_str not in registry_by_pdf:
                    registry_by_pdf[pdf_name_str] = {}
                registry_by_pdf[pdf_name_str] = merge_seeds_into_registry(seeds, registry_by_pdf[pdf_name_str])
                
            else:
                print(f"[seed]   — No status sections found")
        except Exception as e:
            print(f"[seed]   ERROR: {e}")

        # Rate-limit between LLM calls
        if use_llm and i < total:
            time.sleep(llm_rate_limit_sleep)

    save_registry(registry, registry_path)
    
    # Save the by-pdf registry for human verification
    by_pdf_path = registry_path.parent / "scheme_seed_registry_by_pdf.json"
    with open(by_pdf_path, "w", encoding="utf-8") as f:
        json.dump(registry_by_pdf, f, indent=2, ensure_ascii=False)
        
    print(f"\n[seed] Registry saved: {registry_path}")
    print(f"[seed] By-PDF Audit file saved: {by_pdf_path}")
    print(f"[seed] Meetings covered: {sorted(registry.keys())}")
    total_seeds = sum(len(v) for v in registry.values())
    print(f"[seed] Total scheme name seeds: {total_seeds}")

    return registry


def get_seeds_for_meeting(meeting_label: str, registry: dict[str, list[str]]) -> list[str]:
    """
    Get the known scheme names for a given meeting label.
    Fuzzy match: try exact, then case-insensitive, then partial.
    """
    # Exact
    if meeting_label in registry:
        return registry[meeting_label]

    # Case-insensitive
    for key, val in registry.items():
        if key.lower() == meeting_label.lower():
            return val

    # Try extracting number
    m = re.match(r"(\d+)", meeting_label)
    if m:
        n = m.group(1)
        for key, val in registry.items():
            if re.match(rf"^{n}\b", key):
                return val

    return []


# ── CLI ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage:")
        print("  python -m nct_extraction.seed_extractor <pdf_dir>   # Build registry")
        print("  python -m nct_extraction.seed_extractor <pdf_file>  # Scan one PDF")
        sys.exit(1)

    target = Path(sys.argv[1])
    if target.is_dir():
        build_seed_registry(str(target))
    elif target.is_file() and target.suffix.lower() == ".pdf":
        seeds = extract_seeds_from_pdf(str(target))
        print(json.dumps(seeds, indent=2, ensure_ascii=False))
    else:
        print(f"Error: '{target}' is not a valid PDF or directory")
        sys.exit(1)
