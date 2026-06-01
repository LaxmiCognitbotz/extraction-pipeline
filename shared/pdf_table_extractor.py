"""
shared/pdf_table_extractor.py
─────────────────────────────
Single source-of-truth for PDF → LLM text bundle conversion.
Used by every extraction agent in the pipeline.

Design goals
────────────
1. PRESERVE PAGE STRUCTURE — page number headers, section dividers, and the
   "=== PAGE TEXT ===" block are always emitted first so the LLM can read
   the human-readable flow before consulting the structured table.

2. PRESERVE TABLE STRUCTURE — multi-row headers are collapsed into a
   "COLUMN PATHS" legend (Parent > Sub > Leaf) so the LLM never mistakes
   a header row for a data row.  Ragged rows are padded to a uniform width.
   Duplicate / near-duplicate tables (same first-row fingerprint) are
   suppressed.

3. PREVENT HALLUCINATION — cell content that spans multiple lines in the
   PDF is kept on ONE line with a " ⏎ " separator, so vertical structure
   is never confused with horizontal structure.  Purely-empty rows are
   dropped before the LLM sees them.

4. STRATEGY CASCADE — three extraction strategies are tried in order of
   reliability (lines → text+lines → text+text).  The first strategy that
   returns a non-empty, non-degenerate table wins; later strategies are
   only used as fallbacks, never mixed with a successful one.
"""

from __future__ import annotations

import re
import logging
from pathlib import Path
from typing import Any

import pdfplumber

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

# Intra-cell newline replacement — visually distinct so the LLM can tell it
# is *within* a cell, not a row boundary.
_CELL_NL = " ⏎ "

# Minimum non-empty cells in a row to consider the table non-degenerate.
_MIN_CELLS = 2


# ─────────────────────────────────────────────────────────────────────────────
# Text / cell cleaning
# ─────────────────────────────────────────────────────────────────────────────

def clean_text(text: str) -> str:
    """Normalise extracted page text (not table cells)."""
    if not text:
        return ""
    text = text.replace("\t6", "–")   # pdfplumber artefact
    text = text.replace("\u0000", "") # null bytes
    text = text.replace("\t", "  ")   # tabs → double space (preserve alignment)
    text = re.sub(r" {3,}", "  ", text)  # compress excessive spaces
    return text.strip()


def clean_cell(cell: Any) -> str:
    """
    Normalise a single table cell value to a one-line string.

    Rules:
    - None → empty string
    - Internal newlines → _CELL_NL  (keeps visual distinction from row breaks)
    - \t6 artefact → en-dash
    - Null bytes removed
    - Multiple spaces collapsed to one
    """
    if cell is None:
        return ""
    s = str(cell)
    s = s.replace("\t6", "–")
    s = s.replace("\u0000", "")
    # Replace any run of whitespace-with-newline with the cell-NL marker
    s = re.sub(r"[ \t]*[\r\n]+[ \t]*", _CELL_NL, s)
    s = re.sub(r"[ \t]{2,}", " ", s)
    return s.strip()


# ─────────────────────────────────────────────────────────────────────────────
# Header-row detection
# ─────────────────────────────────────────────────────────────────────────────

# A cell is "data-like" if it is purely numeric, a simple date, or a pure
# fraction — i.e. it cannot be a column header label.
_DATA_CELL_RE = re.compile(
    r"^[\d,\.]+$"                     # pure number / decimal
    r"|^\d{1,2}[-/]\w{2,3}[-/]\d{2,4}$"  # date like 31-Mar-25
    r"|^\d{1,4}/\d{1,4}$",           # fraction like 765/400
)


def _row_is_header(row: list[str]) -> bool:
    """
    Return True if this row looks like a column-label row (not data).

    A row is considered a header if:
    - It has at least one non-empty cell, AND
    - NONE of its non-empty cells look purely data-like.
    """
    non_empty = [c for c in row if c.strip()]
    if not non_empty:
        return False
    return not any(_DATA_CELL_RE.fullmatch(c.strip()) for c in non_empty)


# ─────────────────────────────────────────────────────────────────────────────
# Column-path builder  (handles arbitrary depth: Parent > Mid > Leaf)
# ─────────────────────────────────────────────────────────────────────────────

def _build_col_paths(header_rows: list[list[str]], num_cols: int) -> list[str]:
    """
    Collapse multiple header rows into one column-path string per column.

    Algorithm:
    1. Walk header rows top-to-bottom.
    2. For each cell, if non-empty: append to that column's path with " > ".
    3. If empty: look left to find the nearest non-empty ancestor. We ONLY carry
       forward the segment if the current column and the look-left column shared
       the EXACT SAME path before this row (i.e. they are under the same parent span).
    4. Repeated identical path segments are deduplicated.
    """
    paths: list[str] = [""] * num_cols

    for h_row in header_rows:
        # Snapshot paths before this row to enforce parent-span boundaries
        prev_paths = paths.copy()
        
        for col_idx in range(num_cols):
            cell = h_row[col_idx].strip() if col_idx < len(h_row) else ""

            if cell:
                segment = cell
                if paths[col_idx]:
                    last_seg = paths[col_idx].rsplit(" > ", 1)[-1]
                    if last_seg != segment:
                        paths[col_idx] += " > " + segment
                else:
                    paths[col_idx] = segment
            else:
                # Empty cell — check if it can receive a span from the left
                if col_idx > 0:
                    for look in range(col_idx - 1, -1, -1):
                        if look < len(h_row) and h_row[look].strip():
                            # Only span if they share the same parent hierarchy
                            if prev_paths[look] == prev_paths[col_idx]:
                                segment = h_row[look].strip()
                                if paths[col_idx]:
                                    last_seg = paths[col_idx].rsplit(" > ", 1)[-1]
                                    if last_seg != segment:
                                        paths[col_idx] += " > " + segment
                                else:
                                    paths[col_idx] = segment
                            break

    return paths


# ─────────────────────────────────────────────────────────────────────────────
# Table → annotated markdown
# ─────────────────────────────────────────────────────────────────────────────

def table_to_annotated_markdown(table: list[list[Any]]) -> str:
    """
    Convert a raw pdfplumber table to the annotated markdown format used by
    all extraction agents.

    Output format
    ─────────────
    [COLUMN PATHS]
      Col 0: Sl.No
      Col 1: Application ID
      Col 7: Additional Margin on existing / UC system > 220kV level
      …

    | Sl.No | Application ID | … | Additional Margin … > 220kV level | … |
    | ----- | -------------- | … | ---------------------------------- | … |
    | 1     | LTA/2021/001   | … | 450                                | … |
    …

    Design decisions
    ────────────────
    - Completely-empty rows are dropped before processing.
    - All rows are padded to the same column count.
    - Multi-row headers are merged into column-path strings.
    - Data rows are emitted as a standard pipe-table.
    """
    if not table:
        return ""

    # ── Step 1: clean all cells ──────────────────────────────────────────────
    cleaned: list[list[str]] = [
        [clean_cell(c) for c in row]
        for row in table
    ]

    # ── Step 2: drop completely empty rows ───────────────────────────────────
    cleaned = [r for r in cleaned if any(c.strip() for c in r)]
    if not cleaned:
        return ""

    # ── Step 3: uniform column count (pad ragged rows) ───────────────────────
    num_cols = max(len(r) for r in cleaned)
    for row in cleaned:
        while len(row) < num_cols:
            row.append("")

    # ── Step 4: split header rows from data rows ─────────────────────────────
    header_rows: list[list[str]] = []
    idx = 0
    seen_multi_cell = False
    while idx < len(cleaned) and _row_is_header(cleaned[idx]):
        non_empty_count = sum(1 for c in cleaned[idx] if c.strip())
        
        # If we see a single-cell row AFTER seeing a multi-cell row, it's a category data row (e.g. "Gujarat")
        if seen_multi_cell and non_empty_count == 1:
            break
            
        if non_empty_count > 1:
            seen_multi_cell = True
            
        header_rows.append(cleaned[idx])
        idx += 1
    data_rows = cleaned[idx:]

    # Fallback: always need at least one header row for the legend
    if not header_rows:
        header_rows = [cleaned[0]]
        data_rows = cleaned[1:]

    # ── Step 5: build column-path legend ─────────────────────────────────────
    col_paths = _build_col_paths(header_rows, num_cols)

    # ── Step 6: emit [COLUMN PATHS] legend ───────────────────────────────────
    out_lines: list[str] = ["[COLUMN PATHS — map column index to JSON key path]"]
    for i, path in enumerate(col_paths):
        out_lines.append(f"  Col {i}: {path or '(unlabeled)'}")
    out_lines.append("")

    # ── Step 7: emit pipe-table ───────────────────────────────────────────────
    # Use column paths as the header row (already resolved, one row only)
    display_headers = [p or f"Col{i}" for i, p in enumerate(col_paths)]
    col_widths = [max(len(h), 3) for h in display_headers]
    for row in data_rows:
        for j, cell in enumerate(row):
            if j < num_cols:
                col_widths[j] = max(col_widths[j], len(cell))

    def _fmt_row(cells: list[str]) -> str:
        padded = [
            cells[j].ljust(col_widths[j]) if j < len(cells) else " " * col_widths[j]
            for j in range(num_cols)
        ]
        return "| " + " | ".join(padded) + " |"

    out_lines.append(_fmt_row(display_headers))
    out_lines.append("| " + " | ".join("-" * w for w in col_widths) + " |")
    for row in data_rows:
        out_lines.append(_fmt_row(row))

    return "\n".join(out_lines)


# ─────────────────────────────────────────────────────────────────────────────
# Table extraction strategy cascade
# ─────────────────────────────────────────────────────────────────────────────

_STRATEGIES: list[dict] = [
    # Strategy 1: ruled lines — best for bordered/gridded tables
    {
        "vertical_strategy":   "lines",
        "horizontal_strategy": "lines",
        "snap_tolerance":      4,
        "join_tolerance":      4,
        "edge_min_length":     3,
        "min_words_vertical":  1,
        "min_words_horizontal": 1,
        "intersection_tolerance": 3,
    },
    # Strategy 2: text columns + line rows — semi-structured tables
    {
        "vertical_strategy":   "text",
        "horizontal_strategy": "lines",
        "snap_tolerance":      4,
        "join_tolerance":      4,
        "intersection_tolerance": 3,
    },
    # Strategy 3: fully text-based — borderless / implicit-column tables
    {
        "vertical_strategy":   "text",
        "horizontal_strategy": "text",
        "snap_tolerance":      3,
        "join_tolerance":      3,
    },
]


def _table_is_degenerate(table: list[list[Any]]) -> bool:
    """
    True if the table has too few meaningful cells to be useful.
    Degeneracy check prevents polluting the LLM context with noise.
    """
    non_empty_rows = 0
    for row in table:
        if sum(1 for c in row if c is not None and str(c).strip()) >= _MIN_CELLS:
            non_empty_rows += 1
    return non_empty_rows < 2  # need at least header + 1 data row


def _table_fingerprint(table: list[list[Any]]) -> str:
    """Fingerprint = first non-empty row's cleaned cells joined."""
    for row in table:
        cells = [clean_cell(c) for c in row if c is not None and str(c).strip()]
        if cells:
            return "|".join(cells[:6])
    return ""


def extract_tables_from_page(page: Any) -> list[list[list[Any]]]:
    """
    Try each extraction strategy in order; return the first set of
    non-degenerate tables found.  Never mixes results from two strategies.
    Suppresses duplicate tables (same fingerprint).
    """
    seen_fingerprints: set[str] = set()

    for strategy in _STRATEGIES:
        raw = page.extract_tables(table_settings=strategy) or []
        good: list[list[list[Any]]] = []
        for tbl in raw:
            if _table_is_degenerate(tbl):
                continue
            fp = _table_fingerprint(tbl)
            if fp in seen_fingerprints:
                continue  # duplicate — skip
            seen_fingerprints.add(fp)
            good.append(tbl)

        if good:
            logger.debug(
                "Table extraction: strategy '%s' returned %d table(s)",
                strategy["vertical_strategy"] + "+" + strategy["horizontal_strategy"],
                len(good),
            )
            return good

    return []


# ─────────────────────────────────────────────────────────────────────────────
# Public: build page bundle
# ─────────────────────────────────────────────────────────────────────────────

def build_page_bundle(page: Any, page_num: int, total_pages: int) -> str:
    """
    Build the complete text bundle for one pdfplumber page object.

    Structure
    ─────────
    --- PAGE N of M ---
    === PAGE TEXT ===
    <full extracted text — primary source of truth>

    === TABLE 1 (annotated markdown) ===
    <column-path legend + pipe-table>

    === TABLE 2 … ===    (if multiple tables on page)

    === NO TABLES DETECTED ===   (if no tables found)

    Args:
        page:        pdfplumber page object
        page_num:    1-based page number
        total_pages: total pages in document (for the header)
    """
    parts: list[str] = [f"--- PAGE {page_num} of {total_pages} ---"]

    # ── Page text ─────────────────────────────────────────────────────────
    raw_text = page.extract_text(x_tolerance=3, y_tolerance=3) or ""
    raw_text = clean_text(raw_text)
    parts.append("=== PAGE TEXT ===")
    parts.append(raw_text if raw_text else "(no text on this page)")
    parts.append("")

    # ── Tables ────────────────────────────────────────────────────────────
    tables = extract_tables_from_page(page)
    if tables:
        for i, tbl in enumerate(tables, 1):
            parts.append(f"=== TABLE {i} (annotated markdown) ===")
            parts.append(table_to_annotated_markdown(tbl))
            parts.append("")
    else:
        parts.append("=== NO TABLES DETECTED ===")
        parts.append("")

    return "\n".join(parts)


def build_page_bundles_from_pdf(pdf_path: Path) -> list[tuple[int, str]]:
    """
    Open a PDF and return [(page_num, bundle_text), …] for every page.
    Pages are 1-indexed.
    """
    result: list[tuple[int, str]] = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        total = len(pdf.pages)
        logger.info("[%s] %d page(s)", pdf_path.name, total)
        for i, page in enumerate(pdf.pages, 1):
            bundle = build_page_bundle(page, i, total)
            result.append((i, bundle))
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Bundle truncation  (Disabled per user request)
# ─────────────────────────────────────────────────────────────────────────────

# Limit removed. We now send the entire page contents to guarantee no
# data loss, relying on pages_per_chunk to keep sizes manageable.
DEFAULT_MAX_BUNDLE_CHARS = 100_000_000


def truncate_bundle(bundle: str, max_chars: int = DEFAULT_MAX_BUNDLE_CHARS) -> str:
    """
    Truncation is disabled. Returns the bundle unmodified.
    """
    return bundle
