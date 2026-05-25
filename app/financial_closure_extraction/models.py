"""
Canonical Pydantic models for CTUIL Compliance PDF table extraction.
=====================================================================

Two distinct table types exist across all three compliance PDFs:

  TABLE TYPE 1 → FCDeadlineRow / FCDeadlineTable
  ─────────────────────────────────────────────────
  Source: "List of Connectivity Grantees with Financial Closure document
           Submission Deadlines from <PERIOD>"
  Critical deadline column : due_date_of_fc

  TABLE TYPE 2 → LandDocDeadlineRow / LandDocDeadlineTable
  ──────────────────────────────────────────────────────────
  Source: "List of Connectivity Grantees with Land Document Submission
           Deadlines from <PERIOD>"
  Critical deadline column : due_date_for_submission_of_land_docs

Both types share a common base (_ComplianceRowBase) covering applicant,
project, and connectivity fields.  All string fields are Optional[str]
so the LLM can return null for blank / missing cells without validation errors.

JSON key naming contract (snake_case, enforced by field names):
  Every key in the output JSON is exactly the Python field name defined here.
  The LLM is instructed to use these names — no aliases, no extras.
"""

from __future__ import annotations

from typing import Literal, Optional, Union
from pydantic import BaseModel, Field


# ─────────────────────────────────────────────────────────────────────────────
# Shared base — fields present in EVERY table type
# ─────────────────────────────────────────────────────────────────────────────

class _ComplianceRowBase(BaseModel):
    """Fields shared by every row across all CTUIL compliance tables."""

    # ── Meta (always first) ──────────────────────────────────────────────────
    report_period: str = Field(
        description=(
            "Reporting period string derived from the PDF/table title. "
            "Format: 'January 2026 - March 2026'. "
            "ALWAYS populate this on every row — never leave blank."
        )
    )

    # ── Applicant identity ───────────────────────────────────────────────────
    sl_no: Optional[str] = Field(
        default=None,
        description="Serial number as printed in the left-most column of the table.",
    )
    application_id: Optional[str] = Field(
        default=None,
        description=(
            "Unique connectivity application ID exactly as printed, "
            "e.g. '2200000003' or '0230700013'. Do not truncate or reformat."
        ),
    )
    name_of_applicant: Optional[str] = Field(
        default=None,
        description=(
            "Full legal name of the applicant company exactly as printed, "
            "including abbreviations in parentheses."
        ),
    )
    submission_date: Optional[str] = Field(
        default=None,
        description=(
            "Date the connectivity application was submitted. "
            "Return as DD-MM-YYYY text. "
            "If the source shows a 5-digit Excel serial integer (e.g. 45030), "
            "convert it: serial 45030 → 31-01-2023. "
            "Never return a bare integer."
        ),
    )

    # ── Project details ──────────────────────────────────────────────────────
    region: Optional[str] = Field(
        default=None,
        description="Regional grid acronym exactly as printed: NR, SR, ER, WR, NER.",
    )
    location_of_project: Optional[str] = Field(
        default=None,
        description=(
            "Full location string as printed, including village, tehsil, "
            "district, and state if given."
        ),
    )
    type_of_project: Optional[str] = Field(
        default=None,
        description="Project technology type exactly as printed: Solar, Wind, Hybrid, etc.",
    )
    installed_capacity_mw: Optional[str] = Field(
        default=None,
        description="Installed generation capacity in MW as a string, e.g. '300.00' or '1250'.",
    )
    first_scod_of_generation_project: Optional[str] = Field(
        default=None,
        description=(
            "Scheduled Commercial Operation Date (SCOD) of the generation project "
            "as printed, e.g. '30-Apr-25' or '31-Dec-25'."
        ),
    )

    # ── Connectivity / GNA details ───────────────────────────────────────────
    connectivity_granted_mw: Optional[str] = Field(
        default=None,
        description="Connectivity / GNA capacity granted in MW as a string.",
    )
    substation: Optional[str] = Field(
        default=None,
        description=(
            "Name of the substation at which generation is connected / "
            "connectivity is granted, e.g. 'KPS-3', 'Bikaner-III PS'."
        ),
    )
    date_of_connectivity_intimation_in_principle: Optional[str] = Field(
        default=None,
        description="Date of in-principle connectivity intimation letter exactly as printed.",
    )
    date_of_connectivity_intimation_final: Optional[str] = Field(
        default=None,
        description="Date of final connectivity intimation letter exactly as printed.",
    )
    connectivity_gna_start_date_in_principle: Optional[str] = Field(
        default=None,
        description="In-principle connectivity / GNA start date exactly as printed.",
    )
    connectivity_gna_start_date_firm: Optional[str] = Field(
        default=None,
        description=(
            "Firm / confirmed connectivity or GNA start date exactly as printed. "
            "This is the anchor date from which the FC/land-doc deadline is calculated."
        ),
    )


# ─────────────────────────────────────────────────────────────────────────────
# TABLE TYPE 1 — Financial Closure Deadline
# ─────────────────────────────────────────────────────────────────────────────

class FCDeadlineRow(_ComplianceRowBase):
    """
    One data row from a Financial Closure (FC) Deadline table.

    PDF table title pattern:
      "List of Connectivity Grantees with Financial Closure document
       Submission Deadlines from <PERIOD>"

    Sub-table variants:
      - Transition Cases  → extra fields: criterion_for_applying,
                            scod_as_per_application, present_connectivity_deemed_gna
      - GNA Regulation    → those extra fields will be null

    The critical output column is due_date_of_fc (always last in JSON order).
    """

    # Transition-case-only fields (null in GNA-regulation sub-tables)
    criterion_for_applying: Optional[str] = Field(
        default=None,
        description=(
            "Original criterion under which the applicant applied, e.g. "
            "'L&A', 'LOA or PPA'. Present only in Transition Cases sub-tables."
        ),
    )
    scod_as_per_application: Optional[str] = Field(
        default=None,
        description=(
            "SCOD as per the original application (the 'first date considered'). "
            "Present only in Transition Cases sub-tables, e.g. '30-Apr-25'."
        ),
    )
    present_connectivity_deemed_gna: Optional[str] = Field(
        default=None,
        description=(
            "Present connectivity capacity (MW) or deemed GNA value as printed. "
            "Present only in Transition Cases sub-tables."
        ),
    )

    # Fields present in both sub-table types
    revised_criterion: Optional[str] = Field(
        default=None,
        description=(
            "Revised compliance criterion after regulation update, e.g. "
            "'Land Route', 'Land BG Route', 'Land BG + PPA', 'LOA or PPA'."
        ),
    )
    revised_scod: Optional[str] = Field(
        default=None,
        description="Revised SCOD date if the SCOD was updated, exactly as printed.",
    )

    # PRIMARY DEADLINE — always the last field, always populated in FC tables
    due_date_of_fc: Optional[str] = Field(
        default=None,
        description=(
            "Due date for submission of Financial Closure documents, "
            "exactly as printed (e.g. '22-Feb-26'). "
            "This is the critical deadline. Do NOT confuse with land docs deadline."
        ),
    )


class FCDeadlineTable(BaseModel):
    """Structured container for one Financial Closure Deadline sub-table."""

    table_type: Literal["fc_deadline"] = "fc_deadline"
    table_name: str = Field(
        description=(
            "Snake_case identifier derived from the PDF sub-table heading. "
            "Examples: 'fc_deadline_transition_cases', 'fc_deadline_gna_regulation'."
        )
    )
    rows: list[FCDeadlineRow] = Field(
        description="All data rows extracted from this sub-table. Never skip any row."
    )


# ─────────────────────────────────────────────────────────────────────────────
# TABLE TYPE 2 — Land Document Submission Deadline
# ─────────────────────────────────────────────────────────────────────────────

class LandDocDeadlineRow(_ComplianceRowBase):
    """
    One data row from a Land Document Submission Deadline table.

    PDF table title pattern:
      "List of Connectivity Grantees with Land Document Submission
       Deadlines from <PERIOD>"

    The critical output column is due_date_for_submission_of_land_docs
    (always last in JSON order).
    """

    revised_criterion: Optional[str] = Field(
        default=None,
        description=(
            "Revised compliance criterion after regulation update, e.g. "
            "'Land Route', 'Land BG Route', 'LOA or PPA'."
        ),
    )
    revised_scod: Optional[str] = Field(
        default=None,
        description="Revised SCOD date if updated, exactly as printed.",
    )

    # PRIMARY DEADLINE — always the last field, always populated in land-doc tables
    due_date_for_submission_of_land_docs: Optional[str] = Field(
        default=None,
        description=(
            "Due date for submission of Land documents, exactly as printed. "
            "This is the critical deadline. Do NOT confuse with FC deadline."
        ),
    )


class LandDocDeadlineTable(BaseModel):
    """Structured container for one Land Document Submission Deadline sub-table."""

    table_type: Literal["land_doc_deadline"] = "land_doc_deadline"
    table_name: str = Field(
        description=(
            "Snake_case identifier derived from the PDF sub-table heading. "
            "Example: 'land_doc_deadline_main'."
        )
    )
    rows: list[LandDocDeadlineRow] = Field(
        description="All data rows extracted from this sub-table. Never skip any row."
    )


# ─────────────────────────────────────────────────────────────────────────────
# LLM agent output wrapper — per page-chunk call
# ─────────────────────────────────────────────────────────────────────────────

class PageExtractionResult(BaseModel):
    """
    Structured output returned by the LLM agent for one PDF page-chunk call.

    The agent must classify every table it finds as either FCDeadlineTable
    or LandDocDeadlineTable, never mix fields between the two types.
    """

    report_period: str = Field(
        description=(
            "Report period detected from the table title(s) on this page-chunk. "
            "Format: 'January 2026 - March 2026'. "
            "Return 'Unknown Period' only if genuinely absent from all titles."
        )
    )
    fc_tables: list[FCDeadlineTable] = Field(
        default_factory=list,
        description=(
            "All Financial Closure Deadline tables found in this page-chunk. "
            "Empty list if none present."
        ),
    )
    land_doc_tables: list[LandDocDeadlineTable] = Field(
        default_factory=list,
        description=(
            "All Land Document Submission Deadline tables found in this page-chunk. "
            "Empty list if none present."
        ),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Final file-level output wrapper
# ─────────────────────────────────────────────────────────────────────────────

class FileExtractionResult(BaseModel):
    """Aggregated extraction result for a single PDF file."""

    report_period: str
    source_file: str
    tables: list[Union[FCDeadlineTable, LandDocDeadlineTable]] = Field(
        default_factory=list
    )

    @property
    def total_rows(self) -> int:
        return sum(len(t.rows) for t in self.tables)
