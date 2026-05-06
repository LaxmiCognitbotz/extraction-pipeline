"""Pydantic models for the Element Status Sheet extraction output.

These schemas enforce the exact JSON structure defined in AGENT.md,
providing validation, type coercion, and serialization for every
transmission element extracted from the source PDFs.

When used as ``output_type`` in Pydantic AI, these models also serve
as the structured output schema sent to the LLM -- the model returns
validated instances directly.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, model_validator


# ── Enums ──────────────────────────────────────────────────────────────


class DocType(str, Enum):
    """Supported document types for extraction."""

    RTM_UC_REPORT = "RTM_UC_Report"
    TBCB_COMM_REPORT = "TBCB_Comm_Report"
    TBCB_UC_REPORT = "TBCB_UC_Report"
    NCT_REPORT = "NCT_Report"
    GENERAL = "General"


class ElementStatus(str, Enum):
    """Controlled vocabulary for element status."""

    UNDER_CONSTRUCTION = "Under Construction"
    COMMISSIONED = "Commissioned"


# ── Nested Progress Models ─────────────────────────────────────────────


class PhysProgressTxLine(BaseModel):
    """Physical progress of a transmission line.

    Percentage fields are 0.0-1.0 scale. If the document expresses
    progress as e.g. '83%', convert to 0.83. If '1' means 100%, keep
    as-is.
    """

    length: Optional[float] = Field(
        None, description="Sanctioned length in CKM (Circuit Kilometres)"
    )
    location: Optional[str] = Field(
        None, description="Location milestone or description"
    )
    foundation: Optional[float] = Field(
        None, description="Completed foundation length / count"
    )
    erection: Optional[float] = Field(
        None, description="Completed erection length / count"
    )
    stringing: Optional[float] = Field(
        None, description="Completed stringing length / count"
    )
    foundation_pct: Optional[float] = Field(
        None, description="Foundation completion % (0.0-1.0)"
    )
    erection_pct: Optional[float] = Field(
        None, description="Erection completion % (0.0-1.0)"
    )
    stringing_pct: Optional[float] = Field(
        None, description="Stringing completion % (0.0-1.0)"
    )


class PhysProgressSubstation(BaseModel):
    """Physical progress of a substation."""

    civil_work_pct: Optional[float] = Field(
        None, description="Civil work completion % (0.0-1.0)"
    )
    equipment_received_pct: Optional[float] = Field(
        None, description="Equipment received % (0.0-1.0)"
    )
    equipment_erected_pct: Optional[float] = Field(
        None, description="Equipment erected % (0.0-1.0)"
    )


# ── Main Element Model ─────────────────────────────────────────────────


class TransmissionElement(BaseModel):
    """A single transmission element extracted from a source PDF.

    Maps 1:1 to one row in the Element Status sheet.
    """

    element_code: str = Field(
        ..., description="Unique ID in format EL-XXXXX or EL-UNKNOWN-<index>"
    )
    inter_intra_tx_element: str = Field(
        "", description="Scheme/package short name, e.g. Rj Ph-IV"
    )
    transmission_scheme: str = Field(
        "", description="Full name of the transmission scheme / SPV"
    )
    transmission_scope: str = Field(
        "",
        description="Specific element: the line, bay, or substation being constructed",
    )
    mva: Optional[float] = Field(
        None,
        description="MVA capacity parsed from scope (e.g. 2x1500MVA -> 3000)",
    )
    status: str = Field(
        ..., description="Under Construction or Commissioned"
    )
    approval_nct: str = Field(
        "", description="NCT meeting number, e.g. NCT-47"
    )
    source: str = Field(
        ..., description="Source document identifier"
    )
    tender_issuing_authority: str = Field(
        "", description="Entity that issued the tender, e.g. PGCIL"
    )
    date_of_tender_issuance: str = Field(
        "", description="Date tender was issued (MMM-YY)"
    )
    date_of_bid_submission: str = Field(
        "", description="Date bids were due (MMM-YY)"
    )
    execution_timeline: str = Field(
        "", description="Contract duration, e.g. 24 months"
    )
    tentative_scod: str = Field(
        "", description="Tentative SCOD from tender/award stage (MMM-YY)"
    )
    awarded_to: str = Field(
        "", description="Entity awarded the contract"
    )
    spv_transfer_date: str = Field(
        "", description="SPV transfer date (MMM-YY)"
    )
    project_cost: Optional[float] = Field(
        None, description="Project cost in Crores (Rs. Cr.)"
    )

    phys_progress_tx_line: Optional[PhysProgressTxLine] = Field(
        default_factory=PhysProgressTxLine,
        description="Physical progress of transmission line",
    )
    phys_progress_substation: Optional[PhysProgressSubstation] = Field(
        default_factory=PhysProgressSubstation,
        description="Physical progress of substation",
    )

    original_scod: str = Field(
        "", description="Original SCOD per approval/award (MMM-YY)"
    )
    anticipated_scod: str = Field(
        "", description="Current anticipated / revised SCOD (MMM-YY)"
    )
    remarks: str = Field(
        "", description="Verbatim remarks from the document"
    )

    @model_validator(mode="before")
    @classmethod
    def coerce_null_progress(cls, values: dict) -> dict:
        """Convert null progress objects to empty defaults.

        The LLM may return ``null`` for nested progress objects when no
        physical progress data is available. Pydantic needs a valid
        model instance, so we coerce ``None`` to ``{}``.
        """
        if isinstance(values, dict):
            if values.get("phys_progress_tx_line") is None:
                values["phys_progress_tx_line"] = {}
            if values.get("phys_progress_substation") is None:
                values["phys_progress_substation"] = {}
        return values


# ── Extraction Result Wrapper ──────────────────────────────────────────


class ExtractionResult(BaseModel):
    """Wrapper for the full extraction output of a single document."""

    doc_type: DocType
    region: str = ""
    source_pdf: str = ""
    source_markdown: str = ""
    element_count: int = 0
    elements: list[TransmissionElement] = Field(default_factory=list)
