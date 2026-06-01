"""
Pydantic models for CTUIL Revocation (24.6) PDF extraction.

In Pydantic AI the field `description` IS the instruction sent to the LLM
as part of the JSON schema. All column-mapping context lives here so the
system prompt can remain short behavioral rules only.
"""

from __future__ import annotations

from typing import Optional
from pydantic import BaseModel, Field


class RevocationRecord(BaseModel):
    """One data row from a CTUIL 24.6 Expected Revocation table."""

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

    # ── 14 core columns present in all 9 PDF vintages ─────────────────────────

    application_id: Optional[str] = Field(
        default=None,
        description=(
            "The main Connectivity Application ID — numeric ID only, e.g. '1200003331', '0331400007'. "
            "PDF header is simply 'Application ID'. "
            "In newer PDFs the cell may contain BOTH connectivity and LTA IDs — "
            "extract only the main connectivity ID here. Never put LTA IDs here."
        ),
    )
    lta_id: Optional[str] = Field(
        default=None,
        description=(
            "LTA Application ID(s) if present. Two locations depending on PDF version: "
            "(1) Newer PDFs: embedded in the Application ID cell after 'LTA:' keyword. "
            "(2) Older PDFs: on a line below the applicant name starting with 'LTA:'. "
            "If multiple LTA IDs, join with ' | '. Return null if no LTA ID found."
        ),
    )
    applicant_name: Optional[str] = Field(
        default=None,
        description=(
            "Clean legal name of the applicant only. PDF header is 'Name of Applicant'. "
            "Strip any lines beginning with 'LTA:', 'Connectivity:', 'St-II:' etc. "
            "Those go into lta_id or application_id respectively."
        ),
    )
    region: Optional[str] = Field(
        default=None,
        description=(
            "Region code. PDF header is 'Region'. "
            "Values: WR, NR, SR, ER, NER."
        ),
    )
    criterion: Optional[str] = Field(
        default=None,
        description=(
            "Criterion for applying. PDF header is 'Criterion for applying'. "
            "Values: 'L&FC', 'LoA or PPA', 'PPA', etc."
        ),
    )
    type_of_project: Optional[str] = Field(
        default=None,
        description=(
            "Type of project. PDF header is 'Type of Project'. "
            "Values: Solar, Wind, Hybrid, etc."
        ),
    )
    present_connectivity_deemed_gna: Optional[str] = Field(
        default=None,
        description=(
            "Present connectivity / deemed GNA quantum in MW. "
            "PDF header varies: 'Present Connectivity/deemed GNA', "
            "'Present Maximum Connectivity/deemed GNA', 'Present Connectivity /deemed GNA'. "
            "Extract the numeric MW value as a string (e.g. '250', '69.5'). "
            "MUST be a number — NEVER a date string. "
            "If you see a date in this position, the table row has shifted; re-read from PAGE TEXT."
        ),
    )
    substation: Optional[str] = Field(
        default=None,
        description=(
            "Name of the substation. "
            "PDF header varies: 'Substation at which generation connected/connectivity granted', "
            "'Substation at which generation connected/ connectivity granted'. "
            "Extract the substation name exactly as printed."
        ),
    )
    connectivity_gna_start_date_firm: Optional[str] = Field(
        default=None,
        description=(
            "Connectivity / GNA start date (firm date). "
            "PDF header varies: 'Connectivity / GNA start Date (Firm)', "
            "'Connectivity / GNA start Date (firm)', 'Connectivity/GNA start Date (Firm)'. "
            "Extract the date as printed."
        ),
    )
    connectivity_status: Optional[str] = Field(
        default=None,
        description=(
            "Status of the connectivity / GNA. "
            "PDF header varies: 'Status (Effective/Part effective/To be made effective)', "
            "'Status (Effective/Part effective/To be made effective)'. "
            "Common values: 'Effective', 'Part Effective', 'Not Effective', 'To be made effective'."
        ),
    )
    date_connectivity_gna_made_effective: Optional[str] = Field(
        default=None,
        description=(
            "Expected or actual date of connectivity / GNA made effective. "
            "PDF header varies: "
            "'Expected date of connectivity/ GNA made effective/to be made effective', "
            "'*Expected date of connectivity/ GNA to be made effective', "
            "'Expected date of connectivity/GNA made effective/to be made effective'. "
            "Extract the date as printed. Return null only if genuinely not present."
        ),
    )
    generation_commissioning_status: Optional[str] = Field(
        default=None,
        description=(
            "Generation commissioning status. "
            "PDF header varies: 'Generation commissioning status', "
            "'Ggeneration Schedule status (MW)' (note the double-g typo in some PDFs). "
            "Common values: 'Not commissioned', 'Part commissioned', 'Commissioned'. "
            "Some older PDFs store the commissioned MW quantum here — extract as printed."
        ),
    )
    scod_as_per_application: Optional[str] = Field(
        default=None,
        description=(
            "SCOD as per the original application (first date considered). "
            "PDF header varies: 'SCOD as per application (First date considered)'. "
            "Extract the date exactly as printed (e.g. '31-Mar-25', '30-Jun-2022'). "
            "If the PDF cell is blank, empty, or has no recognizable date, return null. "
            "NEVER return '0' or any non-date numeric string for this field."
        ),
    )
    updated_revised_scod: Optional[str] = Field(
        default=None,
        description=(
            "Updated or revised SCOD date. "
            "PDF header varies: 'Updated/Revised SCOD', 'Updated/ Revised SCOD'. "
            "May contain a date, 'NA', or 'No' — extract exactly as printed."
        ),
    )
    compliance_due_date: Optional[str] = Field(
        default=None,
        description=(
            "Due date for compliance of Regulation 24.6. "
            "PDF header varies: '24.6 GNA Compliance due date', "
            "'Due date for compliance of 24.6', '*Due date as per compliance of 24.6'. "
            "Extract the date as printed."
        ),
    )

    class Config:
        populate_by_name = True


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
