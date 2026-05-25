"""
Canonical Pydantic models for CTUIL Bidding Calendar PDF extraction.

Each PDF is a multi-page table with one row per transmission scheme.
Rows are grouped under region headers (Northern Region, Southern Region, etc.)

One record = one transmission scheme entry = one BiddingSchemeRecord.

Fields:
  source_file          : PDF filename
  bidding_calendar_date: Date extracted from filename ("31-03-2026") or
                         explicit mention inside the PDF ("As on dated: 31.10.2023")
  region               : Region header this row falls under
  serial_no            : Sr. No from the table (string, preserved as-is)
  transmission_scheme  : Full bold title of the scheme (first line of the cell)
  major_elements       : Bullet-point sub-items listed below the scheme title
  bidding_agency       : "PFCCL" / "RECPDCL" / etc. from the Bidding Agency column
  bidding_status       : Free-text status narrative from the Bidding Status column
  expected_spv_transfer_date: Date from Expected SPV Transfer Date column, or null

Field descriptions carry the full mapping so the system prompt stays minimal.
"""

from __future__ import annotations

from typing import Optional
from pydantic import BaseModel, Field


class BiddingSchemeRecord(BaseModel):
    """One row from the CTUIL Bidding Calendar table — one transmission scheme."""

    source_file: str = Field(
        description="Name of the PDF file this record was extracted from."
    )
    bidding_calendar_date: Optional[str] = Field(
        default=None,
        description=(
            "The 'as on' date of this bidding calendar edition, in DD-MM-YYYY format. "
            "Extract from: (1) inside the PDF — look for 'Bidding Calendar as on DD.MM.YYYY' "
            "or 'As on dated: DD.MM.YYYY' in the header; "
            "(2) if not explicit in the text, derive from the filename date "
            "(e.g. '01_Bidding Calendar 31-03-2026.pdf' → '31-03-2026'). "
            "Always return as DD-MM-YYYY string."
        ),
    )
    region: Optional[str] = Field(
        default=None,
        description=(
            "Indian power grid region this scheme belongs to, exactly as printed "
            "in the section header row: 'Northern Region', 'Southern Region', "
            "'Eastern Region', 'Western Region', 'North-Eastern Region'. "
            "Carry forward the last seen region header for every row until a new one appears."
        ),
    )
    transmission_scheme: Optional[str] = Field(
        default=None,
        description=(
            "The bold title/name of the transmission scheme — the first line of the "
            "'Transmission Scheme along with Major Elements' column cell. "
            "Example: 'Transmission system for evacuation of power from REZ in Rajasthan (20GW) "
            "under Phase-III Part H'. "
            "Do NOT include the bullet-point sub-items here — those go in major_elements."
        ),
    )
    major_elements: list[str] = Field(
        default_factory=list,
        description=(
            "Bullet-point sub-items listed below the transmission scheme title, "
            "describing the major physical elements of the scheme. "
            "Each bullet (•, ✓, –, or plain dash) becomes one string in this list. "
            "Strip the bullet character itself. "
            "Example: ['Establishment of 2x1500 MVA 765/400kV substation at Dausa', "
            "'LILO of both circuits of Jaipur (Phagi)- Gwalior 765 kV D/c at Dausa']. "
            "Return an empty list [] if no bullet items are visible."
        ),
    )
    bidding_agency: Optional[str] = Field(
        default=None,
        description=(
            "The bidding agency name from the 'Bidding Agency' column, "
            "e.g. 'PFCCL', 'RECPDCL'. Copy exactly as printed."
        ),
    )
    bidding_status: Optional[str] = Field(
        default=None,
        description=(
            "Full status narrative from the 'Bidding Status' column — "
            "copy all bullet points as a single joined string separated by ' | '. "
            "Example: 'Project awarded in 25th NCT meeting held on 28.11.2024 | "
            "RFP issued on 04.03.2025 | Bid submission scheduled on 09.04.2026'. "
            "Include all status lines — do not truncate."
        ),
    )
    expected_spv_transfer_date: Optional[str] = Field(
        default=None,
        description=(
            "Date from the 'Expected SPV Transfer Date' column, exactly as printed. "
            "Examples: '31.05.2026', '15.05.2026', 'May 2023'. "
            "Return null if the cell is blank or contains only a dash."
        ),
    )


class PageExtractionResult(BaseModel):
    """Structured output returned by the LLM for one page-chunk call."""

    bidding_calendar_date: Optional[str] = Field(
        default=None,
        description=(
            "The 'as on' date detected on this page chunk, DD-MM-YYYY. "
            "Null if not visible on these pages."
        ),
    )
    records: list[BiddingSchemeRecord] = Field(
        default_factory=list,
        description=(
            "All transmission scheme rows extracted from this page chunk. "
            "Preserve the order they appear in the PDF. "
            "Never skip a row even if it looks incomplete."
        ),
    )
