"""
Pydantic models for CTUIL Revocation (24.6) PDF extraction.

Column names differ significantly across PDFs, so only the universally
special-handled fields have dedicated model fields. All remaining PDF
columns are captured verbatim in `row_data` with their exact header names.

Special handling:
  application_id  : Connectivity Application ID only (no LTA text)
  lta_id          : LTA Application ID(s) if present — parsed from the
                    Application ID cell (newer PDFs) or Applicant Name
                    cell (older PDFs). Multiple LTA IDs → joined with ' | '.
  applicant_name  : Clean applicant name only (no LTA/Connectivity lines).

Everything else goes into row_data with the EXACT PDF column header as key.
"""

from __future__ import annotations

from typing import Optional
from pydantic import BaseModel, Field


class RevocationRecord(BaseModel):
    """One row from a CTUIL 24.6 Revocation table."""

    source_file: str = Field(
        description="PDF filename this record was extracted from."
    )
    upto_month: Optional[str] = Field(
        default=None,
        description=(
            "The 'upto' month-year from the PDF title, exactly as written. "
            "Examples: \"Jul'26\", \"Oct'25\", \"Jun'26\". "
            "Look for 'upto Mon\\'YY' or 'Final list Mon\\'YY' in the title."
        ),
    )
    application_id: Optional[str] = Field(
        default=None,
        description=(
            "The Connectivity Application ID — numeric ID only, e.g. '1200003331', '0331400007'. "
            "In newer PDFs the Application ID cell may contain BOTH connectivity and LTA IDs — "
            "extract only the main connectivity app number here. "
            "In older PDFs the Application ID cell is clean — copy as-is. "
            "Never put LTA IDs here."
        ),
    )
    lta_id: Optional[str] = Field(
        default=None,
        description=(
            "LTA Application ID(s) if present. "
            "Found in two places depending on the PDF version: "
            "(1) Newer PDFs: embedded in the Application ID cell after 'LTA:' keyword, "
            "    e.g. 'St-II: 312100010 ... LTA: 0412100011(65 MW)' → '0412100011 (65 MW)'. "
            "(2) Older PDFs: on a line below the applicant name starting with 'LTA:' "
            "    e.g. 'LTA: 1200003326 (100MW) & 1200003327 (500MW)' → '1200003326 (100MW) & 1200003327 (500MW)'. "
            "If multiple LTA IDs, join them with ' | '. "
            "Return null if no LTA ID found."
        ),
    )
    applicant_name: Optional[str] = Field(
        default=None,
        description=(
            "Clean legal name of the applicant only. "
            "Strip any lines beginning with 'LTA:', 'Connectivity:', 'St-II:' etc. "
            "from the name cell — those go in lta_id or application_id."
        ),
    )
    row_data: dict[str, Optional[str]] = Field(
        default_factory=dict,
        description=(
            "All remaining columns from the PDF row, keyed by their EXACT column header "
            "(multi-line headers joined with a single space). "
            "Do NOT include sl_no / Sr. No., application_id, applicant_name columns here "
            "— those are captured above. Include everything else."
        ),
    )


class PageExtractionResult(BaseModel):
    """Structured output for one page-chunk LLM call."""

    upto_month: Optional[str] = Field(
        default=None,
        description=(
            "The 'upto' month-year from the PDF title visible on this page chunk. "
            "Null if not visible."
        ),
    )
    records: list[RevocationRecord] = Field(
        default_factory=list,
        description=(
            "All data rows extracted from this chunk. "
            "Preserve row order. Never skip a row."
        ),
    )
