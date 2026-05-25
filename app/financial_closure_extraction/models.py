"""
Canonical Pydantic models for CTUIL Compliance PDF table extraction.

Two table types exist across all compliance PDFs:
  FCDeadlineRow        → "Financial Closure" tables  (due_date_of_fc)
  LandDocDeadlineRow   → "Land Document" tables      (due_date_for_submission_of_land_docs)

Design decisions:
  - sl_no is intentionally excluded (carries no analytical value).
  - Column name variants across PDFs are normalised to one canonical field:
      "SCOD as per application"         → first_scod_of_generation_project
      "Present Connectivity/deemed GNA" → connectivity_granted_mw
      "Updated/Revised SCOD"            → revised_scod
  - All string fields are Optional[str] so blank cells → null without errors.
  - Field descriptions list all PDF column header variants so Pydantic-AI
    can instruct the LLM precisely via the injected schema.
"""

from __future__ import annotations

from typing import Literal, Optional, Union
from pydantic import AliasChoices, BaseModel, ConfigDict, Field


# ─────────────────────────────────────────────────────────────────────────────
# Shared base
# ─────────────────────────────────────────────────────────────────────────────

class _ComplianceRowBase(BaseModel):
    """Fields present in every row across all table types."""

    # populate_by_name=True lets both the canonical field name AND any alias
    # work as input keys — critical because Pydantic-AI injects the schema
    # using field names, while raw PDF headers may differ.
    model_config = ConfigDict(populate_by_name=True)

    report_period: str = Field(
        description="Reporting period from the table title, e.g. 'January 2026 - March 2026'."
    )
    application_id: Optional[str] = Field(
        default=None,
        description="Unique application ID exactly as printed, e.g. '2200000003'.",
    )
    name_of_applicant: Optional[str] = Field(
        default=None,
        description="Full legal name of the applicant exactly as printed.",
    )
    submission_date: Optional[str] = Field(
        default=None,
        description=(
            "Date the application was submitted. Return as DD-MM-YYYY. "
            "If the source shows a 5-digit Excel serial, convert it first."
        ),
    )
    region: Optional[str] = Field(
        default=None,
        description="Regional grid acronym as printed: NR, SR, ER, WR, NER.",
    )
    location_of_project: Optional[str] = Field(
        default=None,
        description="Full location string as printed.",
    )
    type_of_project: Optional[str] = Field(
        default=None,
        description="Technology type as printed: Solar, Wind, Hybrid, etc.",
    )
    installed_capacity_mw: Optional[str] = Field(
        default=None,
        description="Installed generation capacity in MW as a string, e.g. '300.00'.",
    )
    first_scod_of_generation_project: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices(
            "first_scod_of_generation_project",        # canonical (LLM output)
            "SCOD as per application",                 # Transition Cases PDF header
            "First SCOD of Generation Project",        # GNA / Land Doc PDF header
        ),
        description=(
            "Primary SCOD date as printed. "
            "PDF column variants: 'SCOD as per application (First date considered)', "
            "'First SCOD of Generation Project'."
        ),
    )
    connectivity_granted_mw: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices(
            "connectivity_granted_mw",                 # canonical
            "Connectivity granted (MW)",               # GNA & Land Doc PDF header
            "Present Connectivity /deemed GNA",        # Transition Cases PDF header
        ),
        description=(
            "Connectivity/GNA capacity in MW as a string. "
            "PDF column variants: 'Connectivity granted (MW)', "
            "'Present Connectivity /deemed GNA'."
        ),
    )
    substation: Optional[str] = Field(
        default=None,
        description=(
            "Substation name as printed. "
            "PDF column: 'Substation at which generation connected / connectivity granted'."
        ),
    )
    date_of_connectivity_intimation_in_principle: Optional[str] = Field(
        default=None,
        description=(
            "In-principle connectivity intimation date as printed. "
            "PDF column variants: 'Date of Connectivity Intimation (in-principle)', "
            "'Connectivity start date (In-principle)'."
        ),
    )
    date_of_connectivity_intimation_final: Optional[str] = Field(
        default=None,
        description="Final connectivity intimation date as printed.",
    )
    connectivity_gna_start_date_in_principle: Optional[str] = Field(
        default=None,
        description=(
            "In-principle connectivity/GNA start date as printed. "
            "PDF column: 'Connectivity start date (In-principle)' or "
            "'Connectivity / GNA start Date (In-principle)'."
        ),
    )
    connectivity_gna_start_date_firm: Optional[str] = Field(
        default=None,
        description=(
            "Firm connectivity/GNA start date as printed. "
            "PDF column: 'Connectivity / GNA start Date (Firm)'."
        ),
    )


# ─────────────────────────────────────────────────────────────────────────────
# TABLE TYPE 1 — Financial Closure Deadline
# ─────────────────────────────────────────────────────────────────────────────

class FCDeadlineRow(_ComplianceRowBase):
    """
    One row from a Financial Closure Deadline table.
    Sub-types: Transition Cases / GNA Regulation.
    Critical column: due_date_of_fc (always last).
    """

    # Transition Cases only
    criterion_for_applying: Optional[str] = Field(
        default=None,
        description=(
            "Original criterion under which applicant applied. "
            "PDF column: 'Criterion for applying'. "
            "Present only in Transition Cases sub-tables."
        ),
    )

    # GNA Regulation + sometimes Transition Cases
    revised_criterion: Optional[str] = Field(
        default=None,
        description=(
            "Revised compliance criterion, e.g. 'Land Route', 'LOA or PPA'. "
            "PDF column: 'Revised Criterion'."
        ),
    )
    revised_scod: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices(
            "revised_scod",                    # canonical
            "Revised SCOD",                    # standard PDF header
            "Revised SCOD if applicable",      # June-Aug 2025 GNA variant
            "Updated/Revised SCOD",            # June-Aug 2025 Transition variant
        ),
        description=(
            "Revised SCOD if updated, exactly as printed. "
            "PDF variants: 'Revised SCOD', 'Revised SCOD if applicable', "
            "'Updated/Revised SCOD'."
        ),
    )

    # June-Aug 2025 Transition Cases only
    application_status: Optional[str] = Field(
        default=None,
        description=(
            "Application status as printed. "
            "PDF column: 'Application status (granted/agreed/withdrawn/revoked)'. "
            "Present only in June-Aug 2025 Transition Cases."
        ),
    )

    # PRIMARY DEADLINE
    due_date_of_fc: Optional[str] = Field(
        default=None,
        description=(
            "Due date for Financial Closure document submission, exactly as printed. "
            "PDF column: 'Due date of FC'. "
            "This is the critical deadline for FC tables."
        ),
    )


class FCDeadlineTable(BaseModel):
    table_type: Literal["fc_deadline"] = "fc_deadline"
    table_name: str = Field(
        description=(
            "Snake_case name from the PDF sub-heading: "
            "'fc_deadline_transition_cases', 'fc_deadline_gna_regulation', 'fc_deadline_main'."
        )
    )
    rows: list[FCDeadlineRow] = Field(
        description="All data rows. Never skip any row."
    )


# ─────────────────────────────────────────────────────────────────────────────
# TABLE TYPE 2 — Land Document Submission Deadline
# ─────────────────────────────────────────────────────────────────────────────

class LandDocDeadlineRow(_ComplianceRowBase):
    """
    One row from a Land Document Submission Deadline table.
    Critical column: due_date_for_submission_of_land_docs (always last).
    """

    # PRIMARY DEADLINE
    due_date_for_submission_of_land_docs: Optional[str] = Field(
        default=None,
        description=(
            "Due date for Land document submission, exactly as printed. "
            "PDF column: 'Due date for submission of land docs'. "
            "This is the critical deadline for Land Doc tables."
        ),
    )


class LandDocDeadlineTable(BaseModel):
    table_type: Literal["land_doc_deadline"] = "land_doc_deadline"
    table_name: str = Field(
        description="Snake_case name: 'land_doc_deadline_main'."
    )
    rows: list[LandDocDeadlineRow] = Field(
        description="All data rows. Never skip any row."
    )


# ─────────────────────────────────────────────────────────────────────────────
# LLM agent output — per page-chunk call
# ─────────────────────────────────────────────────────────────────────────────

class PageExtractionResult(BaseModel):
    """Structured output for one PDF page-chunk LLM call."""

    report_period: str = Field(
        description=(
            "Period from the table title, format 'Month YYYY - Month YYYY'. "
            "Return 'Unknown Period' only if genuinely absent."
        )
    )
    fc_tables: list[FCDeadlineTable] = Field(
        default_factory=list,
        description="All Financial Closure tables in this chunk. Empty list if none.",
    )
    land_doc_tables: list[LandDocDeadlineTable] = Field(
        default_factory=list,
        description="All Land Document tables in this chunk. Empty list if none.",
    )


# ─────────────────────────────────────────────────────────────────────────────
# File-level result
# ─────────────────────────────────────────────────────────────────────────────

class FileExtractionResult(BaseModel):
    report_period: str
    source_file: str
    tables: list[Union[FCDeadlineTable, LandDocDeadlineTable]] = Field(
        default_factory=list
    )

    @property
    def total_rows(self) -> int:
        return sum(len(t.rows) for t in self.tables)
