"""Pydantic models for Element Status Sheet extraction.

These schemas match the EXACT column structure of element_status_sheet.xlsx.
The ``TransmissionElement`` model IS the LLM instruction — each field's
``description`` tells the model exactly what to extract.

Column mapping (Excel → Pydantic):
  B  Element Code               → element_code (auto-generated post-process)
  C  Inter/Intra Tx. Element    → inter_intra_tx_element (auto-generated post-process)
  D  Transmission Scheme        → transmission_scheme
  E  Transmission Scope         → transmission_scope
  F  MVA                        → mva
  G  Status                     → status (set by doc_type post-process)
  H  Approval NCT               → approval_nct
  I  Source                     → source (set by doc_type post-process)
  O  Awarded To                 → awarded_to
  Q  SPV Transfer Date          → spv_transfer_date
  R  Length                     → tx_length
  S  Location                  → tx_location
  T  Foundation                 → tx_foundation
  U  Erection                   → tx_erection
  V  Stringing                  → tx_stringing
  W  Foundation (%)             → tx_foundation_pct (computed post-process)
  X  Erection (%)               → tx_erection_pct (computed post-process)
  Y  Stringing (%)              → tx_stringing_pct (computed post-process)
  Z  Civil Work (%)             → ss_civil_work_pct
  AA Equipment Received (%)     → ss_equipment_received_pct
  AB Equipment Erected (%)      → ss_equipment_erected_pct
  AC Original SCOD              → original_scod
  AD Anticipated SCOD           → anticipated_scod
  AE Remarks                    → remarks

Columns NOT extracted (not discussed yet):
  J  Tender Issuing Authority
  K  Date of tender issuance
  L  Date of Bid Submission
  M  Execution Timeline
  N  Tentative SCOD
  P  Project Cost (Cr.) (NCT)
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# ── Enums ──────────────────────────────────────────────────────────────


class DocType(str, Enum):
    """Supported document types for extraction."""

    RTM_UC_REPORT = "RTM_UC_Report"
    TBCB_COMM_REPORT = "TBCB_Comm_Report"
    TBCB_UC_REPORT = "TBCB_UC_Report"
    NCT_REPORT = "NCT_Report"
    GENERAL = "General"


# ── Main Element Model (= Excel Row) ──────────────────────────────────


class TransmissionElement(BaseModel):
    """One row in the Element Status Sheet.

    Extract exactly these fields from the CEA/CTUIL transmission report
    table data.  The table has a parent-child structure:

    - **Parent rows**: Numbered (1, 2, 3…), contain the full transmission
      scheme name (project/SPV name), executing agency, SPV transfer date.
    - **Child rows**: Under each parent, contain the specific element
      (line, substation, ICT, bay) with physical progress data.

    Every child row is one element.  Extract ALL of them.
    """

    # ── Col D: Transmission Scheme ─────────────────────────────────
    transmission_scheme: str = Field(
        "",
        description=(
            "Full name of the transmission scheme / project / SPV. "
            "This appears in the numbered parent row. "
            "Example: 'Transmission system strengthening scheme for "
            "evacuation of power from solar energy zones in Rajasthan "
            "(Phase-II) (Part-G)'. "
            "For child rows that don't repeat the scheme name, leave empty."
        ),
    )

    # ── Col E: Transmission Scope ──────────────────────────────────
    transmission_scope: str = Field(
        "",
        description=(
            "Specific element being constructed or commissioned. "
            "Examples: 'Khetri-Narela 765kV D/C Line', "
            "'3x1500MVA, 765/400kV GIS substation at Narela', "
            "'LILO of 765kV S/c Meerut-Bhiwani line at Narela'. "
            "Extract verbatim from the table."
        ),
    )

    # ── Col F: MVA ─────────────────────────────────────────────────
    mva: Optional[float] = Field(
        None,
        description=(
            "Total MVA capacity as a single number. "
            "Compute from scope: '3x1500MVA' -> 4500, '2x500MVA' -> 1000. "
            "Only for substations/ICTs. null for transmission lines."
        ),
    )

    # ── Col H: Approval NCT ───────────────────────────────────────
    approval_nct: str = Field(
        "",
        description=(
            "NCT meeting number(s) where the element was approved. "
            "Example: 'NCT-47' or 'NCT-38, NCT-42'. "
            "Leave empty if not mentioned."
        ),
    )

    # ── Col O: Awarded To ──────────────────────────────────────────
    awarded_to: str = Field(
        "",
        description=(
            "Entity executing the project / awarded the contract. "
            "Examples: 'PGCIL', 'Sterlite Power', 'Adani Transmission', "
            "'Tata Projects'. Extract from the parent row."
        ),
    )

    # ── Col Q: SPV Transfer Date ───────────────────────────────────
    spv_transfer_date: str = Field(
        "",
        description=(
            "Date of transfer of SPV, in MMM-YY format. "
            "Examples: 'May-22', 'Mar-23', 'Oct-24'. "
            "Extract from the parent row."
        ),
    )

    # ── Cols R-V: Physical Progress — Transmission Line ────────────
    tx_length: Optional[float] = Field(
        None,
        description=(
            "Sanctioned length of transmission line in CKM. "
            "Example: 340, 628. Only for transmission lines, null for substations."
        ),
    )
    tx_location: Optional[float] = Field(
        None,
        description=(
            "Total tower locations (number of towers sanctioned). "
            "Example: 463, 816. Only for transmission lines."
        ),
    )
    tx_foundation: Optional[float] = Field(
        None,
        description=(
            "Foundation completed (number of tower foundations done). "
            "Example: 463. Only for transmission lines."
        ),
    )
    tx_erection: Optional[float] = Field(
        None,
        description=(
            "Erection completed (number of towers erected). "
            "Example: 463. Only for transmission lines."
        ),
    )
    tx_stringing: Optional[float] = Field(
        None,
        description=(
            "Stringing completed in CKM. "
            "Example: 340, 524.24. Only for transmission lines."
        ),
    )

    # ── Cols Z-AB: Physical Progress — Substation ──────────────────
    ss_civil_work_pct: Optional[float] = Field(
        None,
        description=(
            "Civil work completion percentage for substation (0.0 to 1.0). "
            "'100%' -> 1.0, '85%' -> 0.85. "
            "Only for substations/ICTs, null for transmission lines."
        ),
    )
    ss_equipment_received_pct: Optional[float] = Field(
        None,
        description=(
            "Equipment received percentage for substation (0.0 to 1.0). "
            "Only for substations/ICTs."
        ),
    )
    ss_equipment_erected_pct: Optional[float] = Field(
        None,
        description=(
            "Equipment erected percentage for substation (0.0 to 1.0). "
            "Only for substations/ICTs."
        ),
    )

    # ── Cols AC-AD: SCOD ───────────────────────────────────────────
    original_scod: str = Field(
        "",
        description=(
            "Original scheduled commissioning date in MMM-YY format. "
            "Example: 'Nov-23', 'Sep-24'."
        ),
    )
    anticipated_scod: str = Field(
        "",
        description=(
            "Current anticipated / revised commissioning date. "
            "Example: 'Dec - 25', 'Mar-26'."
        ),
    )

    # ── Col AE: Remarks ────────────────────────────────────────────
    remarks: str = Field(
        "",
        description=(
            "Verbatim remarks from the report. Include RoW issues, "
            "forest clearance status, charging dates, land acquisition "
            "notes. Do NOT summarise. Preserve line breaks as \\n."
        ),
    )

    # ── Post-processed fields (filled by business_logic, NOT by LLM) ──
    element_code: str = Field(
        "",
        description="Auto-generated unique ID. Leave empty — filled by post-processing.",
    )
    inter_intra_tx_element: str = Field(
        "",
        description="Auto-generated abbreviation. Leave empty — filled by post-processing.",
    )
    status: str = Field(
        "",
        description="Auto-set from document type. Leave empty — filled by post-processing.",
    )
    source: str = Field(
        "",
        description="Auto-set from document type. Leave empty — filled by post-processing.",
    )
    tx_foundation_pct: Optional[float] = Field(
        None,
        description="Auto-computed: foundation / location. Leave null.",
    )
    tx_erection_pct: Optional[float] = Field(
        None,
        description="Auto-computed: erection / location. Leave null.",
    )
    tx_stringing_pct: Optional[float] = Field(
        None,
        description="Auto-computed: stringing / length. Leave null.",
    )


# ── Extraction Result Wrapper (Internal) ──────────────────────────────


class ExtractionResult(BaseModel):
    """Wrapper for the full extraction output of a single document."""

    doc_type: DocType
    region: str = ""
    source_pdf: str = ""
    source_markdown: str = ""
    element_count: int = 0
    elements: list[TransmissionElement] = Field(default_factory=list)
