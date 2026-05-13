"""Pydantic schemas for NCT Minutes extraction.

Fields map to the columns required for the TBCB UC report:
  Element Code, Scheme Name, Scope, Capacity (MVA),
  Execution Timeline, Source, Tender Issuing Authority,
  Project Cost (Cr.), Length (km)
"""

from __future__ import annotations

from typing import Optional, List

from pydantic import BaseModel, Field


class NCTElement(BaseModel):
    """One row extracted from 'Scope of Transmission Scheme' tables."""

    element_code: str = Field(
        "",
        description=(
            "Serial number from the table (Sl.No. / Sr.No. / S.No.). "
            "Examples: '1', '2', 'A.1', 'i'. Leave empty if not present."
        ),
    )
    scheme_name: str = Field(
        "",
        description=(
            "Full name of the parent transmission scheme or project. "
            "This is usually a heading ABOVE the table or in a summary row. "
            "Examples: 'Transmission system for evacuation of power from REZ "
            "in Rajasthan (20GW) under Phase-III Part F'. "
            "Apply the same scheme_name to every scope row under it."
        ),
    )
    scope: str = Field(
        "",
        description=(
            "Specific element being built — verbatim from the 'Scope' column. "
            "Examples: '765kV D/c line from X to Y', "
            "'3x1500MVA, 765/400kV GIS substation at Z'. "
            "Do NOT put the scheme name here; keep scope separate."
        ),
    )
    capacity_mva: Optional[float] = Field(
        None,
        description=(
            "Total MVA capacity as a single number. "
            "Compute from scope: '3x1500MVA' → 4500, '2x500MVA' → 1000. "
            "Only for substations/ICTs. null for transmission lines."
        ),
    )
    length_km: Optional[float] = Field(
        None,
        description=(
            "Length in km or ckm. Extract from 'Capacity/km' or 'Length' column. "
            "Only for transmission lines. null for substations."
        ),
    )
    execution_timeline: str = Field(
        "",
        description=(
            "Implementation timeline — e.g. '36 months', '30 months from date of allocation', "
            "'31.03.2029'. Extract from 'Implementation Timeline' or 'Timeframe' column."
        ),
    )
    tender_issuing_authority: str = Field(
        "",
        description=(
            "Entity issuing the tender or implementing the project. "
            "E.g. 'PGCIL', 'POWERGRID', 'PFCCL', 'RECPDCL', 'CTUIL'. "
            "Also called 'Implementing Agency' or 'BPC'."
        ),
    )
    project_cost_cr: Optional[float] = Field(
        None,
        description=(
            "Project cost in Indian Crores (₹ Cr.). "
            "Extract from 'Estimated Cost', 'Cost Estimate (Approx.)', or '₹ Crores' columns. "
            "This may be scheme-level cost shared across all scope rows."
        ),
    )
    implementation_mode: str = Field(
        "",
        description=(
            "TBCB or RTM. Extract from 'Mode of Implementation' column if available."
        ),
    )
    source: str = Field(
        "",
        description="Auto-populated post-extraction. Leave empty.",
    )


class NCTExtractionResult(BaseModel):
    """Wrapper for all elements extracted from one NCT meeting PDF."""

    meeting_name: str = Field(
        "",
        description="Name of the NCT meeting (e.g. '40th NCT Meeting').",
    )
    elements: List[NCTElement] = Field(default_factory=list)

    def to_mapped_dict(self) -> dict:
        """Convert result to a dict mapped exactly to the required downstream report JSON keys."""
        mapped_elements = []
        for elem in self.elements:
            mapped_elements.append({
                "Element Code": elem.element_code,
                "Transmission Scheme": elem.scheme_name,
                "Transmission Scope": elem.scope,
                "MVA": elem.capacity_mva,
                "Physical Progress S/s of Tx. Line": {
                    "Length": elem.length_km
                },
                "Execution Timeline": elem.execution_timeline,
                "Tender Issuing Authority": elem.tender_issuing_authority,
                "Project Cost (Cr.) (NCT)": elem.project_cost_cr,
                "Source": elem.source
            })
        return {
            "meeting_name": self.meeting_name,
            "elements": mapped_elements
        }

