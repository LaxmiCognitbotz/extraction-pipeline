"""
Canonical Pydantic models for CTUIL Renewable Energy Margin PDF extraction.
Features human-readable aliases matching PDF headers exactly, with nested column groups.
"""

from __future__ import annotations

from typing import Optional
from pydantic import BaseModel, Field


# =─────────────────────────────────────────────────────────────────────────────
# Common Sub-Structures
# =─────────────────────────────────────────────────────────────────────────────

class Level220_400(BaseModel):
    """Sub-columns for 220kV and 400kV levels."""
    level_220kv: Optional[str] = Field(None, alias="220kV level")
    level_400kv: Optional[str] = Field(None, alias="400kV level")

    class Config:
        populate_by_name = True


class KVLevelsCapacity765_400_220(BaseModel):
    """Capacity sub-columns for older PDFs (Existing)."""
    level_765_400kv: Optional[str] = Field(None, alias="765/400kV")
    level_400_220kv_or_132kv: Optional[str] = Field(None, alias="400/220kV or 400/132kV")

    class Config:
        populate_by_name = True


class KVLevelsCapacity765_400_220_standard(BaseModel):
    """Capacity sub-columns for older PDFs (UC/Planned)."""
    level_765_400kv: Optional[str] = Field(None, alias="765/400kV")
    level_400_220kv: Optional[str] = Field(None, alias="400/220kV")

    class Config:
        populate_by_name = True


class TransformationCapacityMVA(BaseModel):
    """Transformation Capacity column groups (MVA)."""
    existing: Optional[KVLevelsCapacity765_400_220] = Field(None, alias="Existing")
    under_implementation: Optional[KVLevelsCapacity765_400_220_standard] = Field(None, alias="Under Implementation")
    planned: Optional[KVLevelsCapacity765_400_220_standard] = Field(None, alias="Planned")

    class Config:
        populate_by_name = True


class REPotentialMW(BaseModel):
    """RE Potential sub-columns."""
    potential: Optional[str] = Field(None, alias="RE Potential [A]")
    bess: Optional[str] = Field(None, alias="BESS [B]")
    evacuation_capacity: Optional[str] = Field(None, alias="S/s Evacuation Capacity [A-B]")

    class Config:
        populate_by_name = True


class KVLevels220_400_Total(BaseModel):
    """Sub-columns for 220kV, 400kV and Total capacity."""
    level_220kv: Optional[str] = Field(None, alias="220kV")
    level_400kv: Optional[str] = Field(None, alias="400kV")
    total: Optional[str] = Field(None, alias="Total")

    class Config:
        populate_by_name = True


# =─────────────────────────────────────────────────────────────────────────────
# 1. Non-RE Substations Margin Model
# =─────────────────────────────────────────────────────────────────────────────

class NonRESubstationMarginRecord(BaseModel):
    """One substation row from the Non-RE Substations Margin table."""

    source_file: str = Field(..., alias="Source File")
    as_on_date: Optional[str] = Field(None, alias="As On Date")
    state: Optional[str] = Field(None, alias="State")
    station_name: Optional[str] = Field(None, alias="Name of station")
    mva_capacity: Optional[str] = Field(None, alias="Existing / UC/ Planned MVA Capacity")
    allocated_under_process_mw: Optional[str] = Field(None, alias="Capacity Allocated/ Under Process (MW)")
    
    additional_margin_existing: Optional[Level220_400] = Field(
        None, alias="Additional Margin on existing / UC system"
    )
    bays_req_existing: Optional[Level220_400] = Field(
        None, alias="Line Bays required for RE integration"
    )
    additional_margin_ict: Optional[Level220_400] = Field(
        None, alias="Additional Margin with ICT Augmentation"
    )
    bays_req_ict: Optional[Level220_400] = Field(
        None, alias="Line Bays required for RE integration (ICT Augmentation)"
    )
    
    no_of_trfs_required: Optional[str] = Field(None, alias="No. of Trfs required for RE integration")
    remarks: Optional[str] = Field(None, alias="Remarks / Total Addl. Margins")

    class Config:
        populate_by_name = True


class NonRESubstationMarginResult(BaseModel):
    """Structured extraction result for a chunk of Non-RE Substations page."""
    as_on_date: Optional[str] = Field(None, alias="As On Date")
    records: list[NonRESubstationMarginRecord] = Field(default_factory=list)

    class Config:
        populate_by_name = True


# =─────────────────────────────────────────────────────────────────────────────
# 2. Proposed RE Substations Margin Model (Older format)
# =─────────────────────────────────────────────────────────────────────────────

class ProposedRESubstationMarginRecord(BaseModel):
    """One substation row from the older Proposed RE Substation Margin table."""

    source_file: str = Field(..., alias="Source File")
    as_on_date: Optional[str] = Field(None, alias="As On Date")
    state: Optional[str] = Field(None, alias="State")
    station_name: Optional[str] = Field(None, alias="Name of station")
    
    transformation_capacity: Optional[TransformationCapacityMVA] = Field(
        None, alias="Transformation Capacity (MVA)"
    )
    
    allocated_mw: Optional[str] = Field(None, alias="Capacity Allocated (MW)")
    
    additional_margin_existing: Optional[Level220_400] = Field(
        None, alias="Additional Margin on existing / UC system"
    )
    additional_margin_ict: Optional[Level220_400] = Field(
        None, alias="Additional Margin with ICT Augmentation"
    )
    
    no_of_trfs_required: Optional[str] = Field(None, alias="No. of Trfs required for RE integration")
    remarks: Optional[str] = Field(None, alias="Remarks")

    class Config:
        populate_by_name = True


class ProposedRESubstationMarginResult(BaseModel):
    """Structured extraction result for a chunk of Proposed RE Substations page."""
    as_on_date: Optional[str] = Field(None, alias="As On Date")
    records: list[ProposedRESubstationMarginRecord] = Field(default_factory=list)

    class Config:
        populate_by_name = True


# =─────────────────────────────────────────────────────────────────────────────
# 3. RE Substations Margin Model
# =─────────────────────────────────────────────────────────────────────────────

class RESubstationMarginRecord(BaseModel):
    """One pooling station row from the RE Substations Margin table."""

    source_file: str = Field(..., alias="Source File")
    as_on_date: Optional[str] = Field(None, alias="As On Date")
    region: Optional[str] = Field(None, alias="Region")
    category: Optional[str] = Field(None, alias="Category")
    
    sl_no: Optional[str] = Field(None, alias="Sl. No.")
    pooling_station: Optional[str] = Field(None, alias="Pooling Station")
    state: Optional[str] = Field(None, alias="State")
    
    re_potential: Optional[REPotentialMW] = Field(None, alias="RE Potential (MW)")
    expected_cod: Optional[str] = Field(None, alias="Expected CoD")
    
    conn_granted: Optional[KVLevels220_400_Total] = Field(None, alias="Connectivity Granted / Agreed (MW)")
    conn_under_process: Optional[KVLevels220_400_Total] = Field(None, alias="Connectivity Under Process (MW)")
    margin_for_connectivity: Optional[KVLevels220_400_Total] = Field(None, alias="Margin for Connectivity (MW)")
    additional_margin_ict: Optional[KVLevels220_400_Total] = Field(
        None, alias="Additional Margin for Connectivity requiring ICT Augmentation / additional Tr. System (MW)"
    )
    
    gna_effectiveness: Optional[str] = Field(
        None, alias="Effectiveness of GNA for Capacity mentioned under 'Margin for Connectivity'"
    )

    class Config:
        populate_by_name = True


class RESubstationMarginResult(BaseModel):
    """Structured extraction result for a chunk of RE Substations page."""
    as_on_date: Optional[str] = Field(None, alias="As On Date")
    records: list[RESubstationMarginRecord] = Field(default_factory=list)

    class Config:
        populate_by_name = True
