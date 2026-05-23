"""Pydantic schemas for NCT Minutes extraction.

This package extracts transmission schemes/scopes from CEA NCT (National Committee
on Transmission) meeting minutes PDFs.

Downstream reporting needs additional columns (e.g., Status/Source constants,
tender dates, SCOD). Those are produced in a post-processing/mapping step (see
`nct_extraction.reporting`).
"""

from __future__ import annotations

from datetime import date
from typing import List, Optional

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
        None,  # NOTE: kept for backward compatibility; use project_cost_text when available.
        description=(
            "Project cost in Indian Crores (₹ Cr.). "
            "Extract from 'Estimated Cost', 'Cost Estimate (Approx.)', or '₹ Crores' columns. "
            "This may be scheme-level cost shared across all scope rows."
        ),
    )
    project_cost_text: Optional[str] = Field(
        None,
        description=(
            "Project cost as written in the NCT PDF (verbatim). "
            "Examples: '₹ 1,234.56 Cr.' or 'Rs. 950 Cr (approx.)'. "
            "Prefer this over numeric coercion; preserve formatting."
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
    source_pdf: str = Field(
        "",
        description="Source PDF filename.",
    )
    elements: List[NCTElement] = Field(default_factory=list)

class NCTReportRow(BaseModel):
    """Final row shape matching the user's required columns."""

    transmission_scheme: str = Field("", description="Transmission Scheme (from NCT PDFs).")
    transmission_scope: str = Field("", description="Transmission Scope (from NCT PDFs).")
    mva: Optional[int] = Field(None, description="Computed total MVA (main transformers only).")
    status: str = Field("Approved", description="Always 'Approved'.")
    source: str = Field("NCT", description="Always 'NCT'.")
    approval_of_elements_in_which_nct: str = Field(
        "", description="NCT approval identifier (e.g., meeting name/number)."
    )
    tender_issuing_authority: str = Field("", description="BPC/Implementing agency (e.g., RECPDCL, PFCCL).")
    date_of_tender_issuance: Optional[date] = Field(None, description="RFP date from tender docs (first page).")
    date_of_bid_submission: Optional[date] = Field(None, description="Latest bid submission date from amendments.")
    execution_timeline_months: Optional[int] = Field(None, description="Implementation timeframe in months (numeric).")
    tentative_scod: Optional[date] = Field(None, description="Bid date + execution timeline months.")
    awarded_to: str = Field("", description="Successful/ranked bidder from result PDFs.")
    project_cost_cr: str = Field("", description="Project Cost (Cr.) as written in NCT PDFs.")
    length_km: Optional[float] = Field(None, description="Line length in km/ckm from Length/Lenth/CKM column.")
    spv_transfer_date: Optional[date] = Field(None, description="SPV Transfer Date (from tender docs, if available).")

    def to_output_dict(self) -> dict:
        """Map to the exact column names requested by the user."""
        def fmt(d: Optional[date]) -> str:
            return d.isoformat() if d else ""

        return {
            "Transmission Scheme": self.transmission_scheme,
            "Transmission Scope": self.transmission_scope,
            "MVA": self.mva,
            "Status": self.status,
            "Approval of Elements in which NCT": self.approval_of_elements_in_which_nct,
            "Source": self.source,
            "Tender Issuing Authority": self.tender_issuing_authority,
            "Date of tender issuance": fmt(self.date_of_tender_issuance),
            "Date of Bid Submission": fmt(self.date_of_bid_submission),
            "Execution Timeline": self.execution_timeline_months,
            "Tentative SCOD": fmt(self.tentative_scod),
            "Awarded To": self.awarded_to,
            "Project Cost (Cr.)": self.project_cost_cr,
            "Length (km)": self.length_km,
            "SPV Transfer Date": fmt(self.spv_transfer_date),
        }


class NCTReport(BaseModel):
    """Report wrapper for one meeting PDF."""

    meeting_name: str = Field("", description="NCT meeting name.")
    source_pdf: str = Field("", description="Source PDF filename.")
    rows: List[NCTReportRow] = Field(default_factory=list)

    def to_output_dict(self) -> dict:
        return {
            "meeting_name": self.meeting_name,
            "source_pdf": self.source_pdf,
            "rows": [r.to_output_dict() for r in self.rows],
        }
