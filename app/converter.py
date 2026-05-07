"""PDF → Camelot Table Extraction + Smart Chunking.

Production-grade Camelot extraction pipeline for CEA/CTUIL
transmission reports.  Two parsing modes:

  - **Lattice** → tables with visible borders/rules
  - **Stream**  → tables without borders (fallback)

Tables are extracted, normalized (headers cleaned), serialized as
CSV text, then assembled into a single corpus.  The corpus is then
split into LLM-sized chunks using project-boundary-aware chunking.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import camelot
import pandas as pd

from app.config import settings


# ── Data Structures ────────────────────────────────────────────────────


@dataclass
class CamelotTable:
    """A single table extracted by Camelot."""

    page: int
    table_index: int
    df: pd.DataFrame = field(default_factory=pd.DataFrame)
    accuracy: float = 0.0


@dataclass
class CamelotCorpus:
    """Full extraction result from a PDF — all tables as a single text corpus."""

    source_pdf: str = ""
    total_pages: int = 0
    total_tables: int = 0
    tables: list[CamelotTable] = field(default_factory=list)
    corpus_text: str = ""
    corpus_chars: int = 0
    chunks: list[str] = field(default_factory=list)
    debug_markdown: str = ""


# ── Table Normalization ────────────────────────────────────────────────


def _clean_cell(value: str) -> str:
    """Clean a single cell value from Camelot output."""
    if not value or value in ("None", "nan"):
        return ""
    # Remove CID references (embedded font issues)
    cleaned = re.sub(r"\(cid:\d+\)", "", str(value))
    # Collapse whitespace (Camelot may preserve internal newlines)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    # Normalize dashes
    if cleaned in ("-", "–", "—", "nil", "Nil", "NIL", "NA", "N/A"):
        return ""
    return cleaned


def _normalize_table(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize a raw Camelot DataFrame.

    - First row becomes column headers
    - Headers are stripped and lowercased
    - All cells are cleaned
    - Empty rows are dropped
    """
    if df.empty or len(df) < 2:
        return df

    # Use first row as headers
    headers = [_clean_cell(str(c)).lower() for c in df.iloc[0]]
    data = df.iloc[1:]
    data.columns = headers
    data = data.reset_index(drop=True)

    # Clean all cell values
    for col in data.columns:
        data[col] = data[col].apply(lambda x: _clean_cell(str(x)))

    # Drop completely empty rows
    data = data[data.apply(lambda row: any(v.strip() for v in row), axis=1)]

    return data.reset_index(drop=True)


def _table_to_csv_text(df: pd.DataFrame) -> str:
    """Convert a normalized DataFrame to CSV text for LLM consumption.

    CSV is more token-efficient than Markdown tables and easier for
    the LLM to parse column-by-column.
    """
    return df.to_csv(index=False)


def _table_to_markdown(df: pd.DataFrame) -> str:
    """Convert a raw Camelot DataFrame to Markdown table (for debug output)."""
    if df.empty:
        return ""

    lines: list[str] = []
    for row_idx in range(len(df)):
        cells = []
        for col_idx in range(len(df.columns)):
            val = _clean_cell(str(df.iloc[row_idx, col_idx]))
            val = val.replace("|", "\\|")
            cells.append(val)
        lines.append("| " + " | ".join(cells) + " |")
        if row_idx == 0:
            sep = "| " + " | ".join(["---"] * len(cells)) + " |"
            lines.append(sep)

    return "\n".join(lines)


# ── Core Extraction ────────────────────────────────────────────────────


def extract_tables_from_pdf(
    pdf_path: str | Path,
    output_dir: str | Path | None = None,
) -> CamelotCorpus:
    """Extract ALL tables from a PDF using Camelot.

    Strategy:
      1. Try **lattice** mode first for the entire PDF (bordered tables)
      2. If lattice yields 0 tables, retry with **stream** mode
      3. Normalize each table's headers and cells
      4. Serialize all tables as CSV text → single corpus
      5. Smart-chunk the corpus for LLM consumption

    Args:
        pdf_path: Path to the source PDF file.
        output_dir: Optional directory to write debug Markdown.

    Returns:
        ``CamelotCorpus`` with the full text corpus, chunks, and metadata.

    Raises:
        FileNotFoundError: If the PDF does not exist.
    """
    pdf_path = Path(pdf_path).resolve()
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    out_dir = Path(output_dir) if output_dir else settings.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"[converter] Extracting tables from {pdf_path.name} via Camelot ...")

    # Get page count for reporting
    total_pages = _get_page_count(pdf_path)
    print(f"[converter] PDF has {total_pages} pages")

    # Extract all tables at once (much faster than page-by-page)
    raw_tables = _extract_all_tables(pdf_path)

    if not raw_tables:
        print("[converter] [WARN] No tables found in PDF")
        return CamelotCorpus(source_pdf=pdf_path.name, total_pages=total_pages)

    print(f"[converter] Extracted {len(raw_tables)} table(s)")

    # Build corpus from all tables
    corpus = _build_corpus(raw_tables, pdf_path.name, total_pages)

    # Smart-chunk the corpus
    corpus.chunks = chunk_text(corpus.corpus_text)
    print(
        f"[converter] [OK] Corpus: {corpus.corpus_chars:,} chars → "
        f"{len(corpus.chunks)} chunk(s)"
    )

    # Write debug Markdown
    md_path = out_dir / f"{pdf_path.stem}.md"
    md_path.write_text(corpus.debug_markdown, encoding="utf-8")
    print(f"[converter] [OK] Debug Markdown → {md_path}")

    return corpus


def _extract_all_tables(pdf_path: Path) -> list[CamelotTable]:
    """Extract all tables from a PDF using lattice → stream fallback."""
    tables_out: list[CamelotTable] = []

    # Try lattice first (bordered tables — best for CEA reports)
    try:
        tables = camelot.read_pdf(
            str(pdf_path),
            flavor="lattice",
            pages="all",
            strip_text="\n",
        )
        if tables and len(tables) > 0:
            print(f"[converter]   Lattice: {len(tables)} table(s) found")
            for i, tbl in enumerate(tables):
                if not tbl.df.empty:
                    tables_out.append(CamelotTable(
                        page=tbl.page,
                        table_index=i,
                        df=tbl.df,
                        accuracy=tbl.accuracy,
                    ))
            if tables_out:
                avg_acc = sum(t.accuracy for t in tables_out) / len(tables_out)
                print(f"[converter]   Average accuracy: {avg_acc:.1f}%")
                return tables_out
    except Exception as e:
        print(f"[converter]   Lattice failed: {e}")

    # Fallback to stream mode (borderless tables)
    try:
        tables = camelot.read_pdf(
            str(pdf_path),
            flavor="stream",
            pages="all",
            strip_text="\n",
        )
        if tables and len(tables) > 0:
            print(f"[converter]   Stream: {len(tables)} table(s) found")
            for i, tbl in enumerate(tables):
                if not tbl.df.empty:
                    tables_out.append(CamelotTable(
                        page=tbl.page,
                        table_index=i,
                        df=tbl.df,
                        accuracy=tbl.accuracy,
                    ))
    except Exception as e:
        print(f"[converter]   Stream also failed: {e}")

    return tables_out


def _build_corpus(
    tables: list[CamelotTable],
    source_pdf: str,
    total_pages: int,
) -> CamelotCorpus:
    """Build a unified text corpus from all extracted tables.

    Each table is normalized and serialized as CSV text.
    A debug Markdown version is also generated.
    """
    csv_blocks: list[str] = []
    md_sections: list[str] = []

    for tbl in tables:
        normalized = _normalize_table(tbl.df)

        # CSV for LLM
        csv_text = _table_to_csv_text(normalized)
        csv_blocks.append(
            f"--- Table from Page {tbl.page} "
            f"(accuracy: {tbl.accuracy:.1f}%) ---\n{csv_text}"
        )

        # Markdown for debugging
        md_text = _table_to_markdown(tbl.df)
        md_sections.append(
            f"## Page {tbl.page} — Table {tbl.table_index + 1} "
            f"(accuracy: {tbl.accuracy:.1f}%)\n\n{md_text}"
        )

    corpus_text = "\n\n".join(csv_blocks)
    debug_md = "\n\n---\n\n".join(md_sections)

    return CamelotCorpus(
        source_pdf=source_pdf,
        total_pages=total_pages,
        total_tables=len(tables),
        tables=tables,
        corpus_text=corpus_text,
        corpus_chars=len(corpus_text),
        debug_markdown=debug_md,
    )


# ── Smart Chunking ─────────────────────────────────────────────────────


def chunk_text(text: str, max_chars: int = 6000) -> list[str]:
    """Split corpus text into LLM-sized chunks.

    Smart chunking strategy:
      1. Try to break at project/table boundaries ("--- Table from")
      2. Failing that, break at line boundaries near the limit
      3. Never split mid-line

    This ensures each chunk contains coherent table data.

    Args:
        text: Full corpus text (CSV-formatted tables).
        max_chars: Maximum characters per chunk.

    Returns:
        List of text chunks, each <= max_chars.
    """
    if not text:
        return []

    if len(text) <= max_chars:
        return [text]

    chunks: list[str] = []
    start = 0

    while start < len(text):
        end = min(start + max_chars, len(text))

        if end >= len(text):
            # Last chunk — take everything remaining
            chunks.append(text[start:].strip())
            break

        # Strategy 1: Try to break at a table boundary
        table_boundary = text.rfind("--- Table from", start, end)
        if table_boundary > start:
            chunks.append(text[start:table_boundary].strip())
            start = table_boundary
            continue

        # Strategy 2: Try to break at a project-like boundary
        # Look for "Transmission" or numbered project headers
        project_cut = text.rfind("Transmission", start, end)
        if project_cut > start and project_cut > start + 500:
            # Find the start of the line containing "Transmission"
            line_start = text.rfind("\n", start, project_cut)
            if line_start > start:
                chunks.append(text[start:line_start].strip())
                start = line_start + 1
                continue

        # Strategy 3: Break at the last newline within range
        last_newline = text.rfind("\n", start, end)
        if last_newline > start:
            chunks.append(text[start:last_newline].strip())
            start = last_newline + 1
        else:
            # Hard break (shouldn't happen with CSV data)
            chunks.append(text[start:end].strip())
            start = end

    # Filter out empty chunks
    return [c for c in chunks if c.strip()]


# ── Page Count Helper ──────────────────────────────────────────────────


def _get_page_count(pdf_path: Path) -> int:
    """Get the total number of pages in a PDF."""
    try:
        from PyPDF2 import PdfReader
        reader = PdfReader(str(pdf_path))
        return len(reader.pages)
    except ImportError:
        pass

    try:
        import pdfplumber
        with pdfplumber.open(str(pdf_path)) as pdf:
            return len(pdf.pages)
    except ImportError:
        pass

    return 0  # Unknown
