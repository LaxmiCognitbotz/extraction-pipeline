"""
PDF table reader for the langextract revocation extraction module.

Delegates all page-bundle construction to shared.pdf_table_extractor,
which provides:
  - Annotated markdown tables with [COLUMN PATHS] legend
  - Multi-row header detection and span-aware carry-forward
  - Ragged-row padding
  - Duplicate-table suppression
  - ⏎ intra-cell newline markers (so cell content never breaks row structure)

This module is kept as a thin adapter so the rest of the langextract pipeline
(extractor.py, run_pipeline.py) does not need to know about the shared module
directly.
"""

from __future__ import annotations

from pathlib import Path

from shared.pdf_table_extractor import build_page_bundle, build_page_bundles_from_pdf

__all__ = ["read_pdf_page_bundles"]


def read_pdf_page_bundles(pdf_path: Path) -> list[tuple[int, str]]:
    """
    Open a PDF and return [(page_number, bundle_text), …] for every page.
    Page numbers are 1-indexed.

    Each bundle contains:
      --- PAGE N of M ---
      === PAGE TEXT ===
      <full extracted text>

      === TABLE 1 (annotated markdown) ===
      [COLUMN PATHS — …]
        Col 0: …
        …
      | header | … |
      | ------ | … |
      | data   | … |
    """
    return build_page_bundles_from_pdf(pdf_path)
