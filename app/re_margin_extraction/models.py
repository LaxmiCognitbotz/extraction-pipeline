"""
Canonical Pydantic models for CTUIL Renewable Energy Margin PDF extraction.
Features human-readable aliases matching PDF headers exactly, with nested column groups.
Includes pre-validation converters to guarantee nested structures are never null in JSON.
"""

from __future__ import annotations

from typing import Optional, Any
from pydantic import BaseModel, Field, model_validator


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
    
    additional_margin_existing: Level220_400 = Field(
        default_factory=Level220_400, alias="Additional Margin on existing / UC system"
    )
    bays_req_existing: Level220_400 = Field(
        default_factory=Level220_400, alias="Line Bays required for RE integration"
    )
    additional_margin_ict: Level220_400 = Field(
        default_factory=Level220_400, alias="Additional Margin with ICT Augmentation"
    )
    bays_req_ict: Level220_400 = Field(
        default_factory=Level220_400, alias="Line Bays required for RE integration (ICT Augmentation)"
    )
    
    no_of_trfs_required: Optional[str] = Field(None, alias="No. of Trfs required for RE integration")
    remarks: Optional[str] = Field(None, alias="Remarks / Total Addl. Margins")

    @model_validator(mode="before")
    @classmethod
    def ensure_nested_objects(cls, data: Any) -> Any:
        if isinstance(data, dict):
            for key in [
                "Additional Margin on existing / UC system",
                "Line Bays required for RE integration",
                "Additional Margin with ICT Augmentation",
                "Line Bays required for RE integration (ICT Augmentation)",
                "additional_margin_existing",
                "bays_req_existing",
                "additional_margin_ict",
                "bays_req_ict",
            ]:
                if data.get(key) is None:
                    data[key] = {}
        return data

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
    
    transformation_capacity: TransformationCapacityMVA = Field(
        default_factory=TransformationCapacityMVA, alias="Transformation Capacity (MVA)"
    )
    
    allocated_mw: Optional[str] = Field(None, alias="Capacity Allocated (MW)")
    
    additional_margin_existing: Level220_400 = Field(
        default_factory=Level220_400, alias="Additional Margin on existing / UC system"
    )
    additional_margin_ict: Level220_400 = Field(
        default_factory=Level220_400, alias="Additional Margin with ICT Augmentation"
    )
    
    no_of_trfs_required: Optional[str] = Field(None, alias="No. of Trfs required for RE integration")
    remarks: Optional[str] = Field(None, alias="Remarks")

    @model_validator(mode="before")
    @classmethod
    def ensure_nested_objects(cls, data: Any) -> Any:
        if isinstance(data, dict):
            # Nested Transformation Capacity MVA
            tc_key = "Transformation Capacity (MVA)" if "Transformation Capacity (MVA)" in data else "transformation_capacity"
            tc = data.get(tc_key)
            if tc is None:
                data[tc_key] = {
                    "Existing": {},
                    "Under Implementation": {},
                    "Planned": {}
                }
            elif isinstance(tc, dict):
                for sub in ["Existing", "Under Implementation", "Planned", "existing", "under_implementation", "planned"]:
                    if tc.get(sub) is None:
                        tc[sub] = {}
            
            for key in [
                "Additional Margin on existing / UC system",
                "Additional Margin with ICT Augmentation",
                "additional_margin_existing",
                "additional_margin_ict",
            ]:
                if data.get(key) is None:
                    data[key] = {}
        return data

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
    
    pooling_station: Optional[str] = Field(None, alias="Pooling Station")
    state: Optional[str] = Field(None, alias="State")
    
    re_potential: REPotentialMW = Field(
        default_factory=REPotentialMW, alias="RE Potential (MW)"
    )
    expected_cod: Optional[str] = Field(None, alias="Expected CoD")
    
    conn_granted: KVLevels220_400_Total = Field(
        default_factory=KVLevels220_400_Total, alias="Connectivity Granted / Agreed (MW)"
    )
    conn_under_process: KVLevels220_400_Total = Field(
        default_factory=KVLevels220_400_Total, alias="Connectivity Under Process (MW)"
    )
    margin_for_connectivity: KVLevels220_400_Total = Field(
        default_factory=KVLevels220_400_Total, alias="Margin for Connectivity (MW)"
    )
    additional_margin_ict: KVLevels220_400_Total = Field(
        default_factory=KVLevels220_400_Total, alias="Additional Margin for Connectivity requiring ICT Augmentation / additional Tr. System (MW)"
    )
    
    gna_effectiveness: Optional[str] = Field(
        None, alias="Effectiveness of GNA for Capacity mentioned under 'Margin for Connectivity'"
    )

    @model_validator(mode="before")
    @classmethod
    def ensure_nested_objects(cls, data: Any) -> Any:
        if isinstance(data, dict):
            for key in [
                "RE Potential (MW)",
                "Connectivity Granted / Agreed (MW)",
                "Connectivity Under Process (MW)",
                "Margin for Connectivity (MW)",
                "Additional Margin for Connectivity requiring ICT Augmentation / additional Tr. System (MW)",
                "re_potential",
                "conn_granted",
                "conn_under_process",
                "margin_for_connectivity",
                "additional_margin_ict",
            ]:
                if data.get(key) is None:
                    data[key] = {}
        return data

    class Config:
        populate_by_name = True


class RESubstationMarginResult(BaseModel):
    """Structured extraction result for a chunk of RE Substations page."""
    as_on_date: Optional[str] = Field(None, alias="As On Date")
    records: list[RESubstationMarginRecord] = Field(default_factory=list)

    class Config:
        populate_by_name = True
