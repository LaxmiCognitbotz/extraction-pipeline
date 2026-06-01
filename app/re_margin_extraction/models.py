"""
Canonical Pydantic models for CTUIL Renewable Energy Margin PDF extraction.
Features human-readable aliases matching PDF headers exactly, with nested column groups.
Includes pre-validation converters to guarantee nested structures are never null in JSON.

Each *Record model exposes a ``schema_info()`` classmethod that introspects
``model_fields`` to produce the column-path legend and field metadata used by
the agent to build its system prompt dynamically.  No column names are
hardcoded in the agent — the models are the single source of truth.
"""

from __future__ import annotations

from typing import Optional, Any
from pydantic import BaseModel, Field, model_validator
from pydantic.fields import FieldInfo


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

    source_file: str = Field("", alias="Source File")
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

    @classmethod
    def schema_info(cls) -> dict:
        """
        Returns a dict describing the schema for prompt generation:
          - 'pdf_title'       : human label for this report type
          - 'row_noun'        : what each row represents
          - 'carry_forward'   : list of (field_alias, description) that need
                                carry-forward logic (state / region headers)
          - 'scalar_fields'   : [(alias, description), …]
          - 'nested_fields'   : [(parent_alias, [(sub_alias, …), …]), …]
        """
        _INTERNAL = {"Source File", "As On Date"}  # injected; not in PDF table
        _CARRY = {
            "State": "State name header row — carry forward to all subsequent rows until a new state appears.",
        }
        scalar, nested = [], []
        for fname, finfo in cls.model_fields.items():
            alias = finfo.alias or fname
            if alias in _INTERNAL:
                continue
            ann = finfo.annotation
            # Resolve Optional[X] → X
            origin = getattr(ann, "__origin__", None)
            args = getattr(ann, "__args__", ())
            inner = args[0] if (origin is type(None) or origin is None) and args else ann
            if hasattr(inner, "model_fields") and inner is not str:
                sub_fields = [
                    (sf.alias or sn)
                    for sn, sf in inner.model_fields.items()
                ]
                nested.append((alias, sub_fields))
            else:
                scalar.append((alias, _CARRY.get(alias, "")))
        return {
            "pdf_title": "CTUIL Non-RE Substations Margin",
            "row_noun": "substation",
            "carry_forward": [(k, v) for k, v in _CARRY.items()],
            "scalar_fields": scalar,
            "nested_fields": nested,
        }


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

    source_file: str = Field("", alias="Source File")
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

    @classmethod
    def schema_info(cls) -> dict:
        _INTERNAL = {"Source File", "As On Date"}
        _CARRY = {
            "State": "State name header row — carry forward to all subsequent rows until a new state appears.",
        }
        scalar, nested = [], []
        for fname, finfo in cls.model_fields.items():
            alias = finfo.alias or fname
            if alias in _INTERNAL:
                continue
            ann = finfo.annotation
            args = getattr(ann, "__args__", ())
            inner = args[0] if args else ann
            if hasattr(inner, "model_fields") and inner is not str:
                # Two-level nested (e.g. Transformation Capacity > Existing > 765/400kV)
                sub_entries = []
                for mid_name, mid_info in inner.model_fields.items():
                    mid_alias = mid_info.alias or mid_name
                    mid_ann = mid_info.annotation
                    mid_args = getattr(mid_ann, "__args__", ())
                    mid_inner = mid_args[0] if mid_args else mid_ann
                    if hasattr(mid_inner, "model_fields") and mid_inner is not str:
                        leaf_aliases = [
                            (lf.alias or ln)
                            for ln, lf in mid_inner.model_fields.items()
                        ]
                        sub_entries.append((mid_alias, leaf_aliases))
                    else:
                        sub_entries.append((mid_alias, []))
                nested.append((alias, sub_entries))
            else:
                scalar.append((alias, _CARRY.get(alias, "")))
        return {
            "pdf_title": "CTUIL Proposed RE Substations Margin (older reports)",
            "row_noun": "substation",
            "carry_forward": [(k, v) for k, v in _CARRY.items()],
            "scalar_fields": scalar,
            "nested_fields": nested,
        }


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

    source_file: str = Field("", alias="Source File")
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

    @classmethod
    def schema_info(cls) -> dict:
        _INTERNAL = {"Source File", "As On Date"}
        _CARRY = {
            "Region":   "Region header row (e.g. 'Northern Region') — carry forward until a new region appears.",
            "Category": "Category header row (e.g. 'A. Existing RE Pooling Stations') — carry forward until a new category appears.",
        }
        scalar, nested = [], []
        for fname, finfo in cls.model_fields.items():
            alias = finfo.alias or fname
            if alias in _INTERNAL:
                continue
            ann = finfo.annotation
            args = getattr(ann, "__args__", ())
            inner = args[0] if args else ann
            if hasattr(inner, "model_fields") and inner is not str:
                sub_fields = [
                    (sf.alias or sn)
                    for sn, sf in inner.model_fields.items()
                ]
                nested.append((alias, sub_fields))
            else:
                scalar.append((alias, _CARRY.get(alias, "")))
        return {
            "pdf_title": "CTUIL RE Substations Margin",
            "row_noun": "pooling station",
            "carry_forward": [(k, v) for k, v in _CARRY.items()],
            "scalar_fields": scalar,
            "nested_fields": nested,
        }


class RESubstationMarginResult(BaseModel):
    """Structured extraction result for a chunk of RE Substations page."""
    as_on_date: Optional[str] = Field(None, alias="As On Date")
    records: list[RESubstationMarginRecord] = Field(default_factory=list)

    class Config:
        populate_by_name = True


# =─────────────────────────────────────────────────────────────────────────────
# Module-level helper — used by the agent to get schema info by kind string
# =─────────────────────────────────────────────────────────────────────────────

_KIND_TO_RECORD: dict[str, type] = {
    "non-re":          NonRESubstationMarginRecord,
    "proposed-re":     ProposedRESubstationMarginRecord,
    "re-substations":  RESubstationMarginRecord,
}


def get_schema_info(kind: str) -> dict:
    """Return schema_info() for the given PDF kind string."""
    cls = _KIND_TO_RECORD.get(kind)
    if cls is None:
        raise ValueError(f"Unknown kind '{kind}'. Valid: {list(_KIND_TO_RECORD)}")
    return cls.schema_info()


# =─────────────────────────────────────────────────────────────────────────────
# FLAT EXTRACTION MODELS (to prevent duplicate columns and nesting confusion for LLM)
# =─────────────────────────────────────────────────────────────────────────────

class FlatNonRESubstationMarginRecord(BaseModel):
    """Flat extraction model for Non-RE Substations."""
    state: Optional[str] = Field(None, alias="State", description="State name (e.g., Gujarat, Maharashtra) — carry forward if the cell is empty but a previous row established a state.")
    station_name: Optional[str] = Field(None, alias="Name of station", description="Substation name. Strip off trailing voltage levels.")
    mva_capacity: Optional[str] = Field(None, alias="Existing / UC/ Planned MVA Capacity")
    allocated_under_process_mw: Optional[str] = Field(None, alias="Capacity Allocated/ Under Process (MW)")
    
    additional_margin_existing_220kv: Optional[str] = Field(None, alias="Additional Margin on existing / UC system > 220kV level")
    additional_margin_existing_400kv: Optional[str] = Field(None, alias="Additional Margin on existing / UC system > 400kV level")
    
    bays_req_existing_220kv: Optional[str] = Field(None, alias="Line Bays required for RE integration > 220kV level")
    bays_req_existing_400kv: Optional[str] = Field(None, alias="Line Bays required for RE integration > 400kV level")
    
    additional_margin_ict_220kv: Optional[str] = Field(None, alias="Additional Margin with ICT Augmentation > 220kV level")
    additional_margin_ict_400kv: Optional[str] = Field(None, alias="Additional Margin with ICT Augmentation > 400kV level")
    
    bays_req_ict_220kv: Optional[str] = Field(None, alias="Line Bays required for RE integration (ICT Augmentation) > 220kV level")
    bays_req_ict_400kv: Optional[str] = Field(None, alias="Line Bays required for RE integration (ICT Augmentation) > 400kV level")
    
    no_of_trfs_required: Optional[str] = Field(None, alias="No. of Trfs required for RE integration")
    remarks: Optional[str] = Field(None, alias="Remarks / Total Addl. Margins")

    class Config:
        populate_by_name = True


class FlatNonRESubstationMarginResult(BaseModel):
    as_on_date: Optional[str] = Field(None, alias="As On Date")
    records: list[FlatNonRESubstationMarginRecord] = Field(default_factory=list)

    class Config:
        populate_by_name = True


class FlatProposedRESubstationMarginRecord(BaseModel):
    """Flat extraction model for Proposed RE Substations."""
    state: Optional[str] = Field(None, alias="State", description="State name header — carry forward.")
    station_name: Optional[str] = Field(None, alias="Name of station", description="Substation name. Strip off trailing voltage levels.")
    
    tc_existing_765_400: Optional[str] = Field(None, alias="Transformation Capacity (MVA) > Existing > 765/400kV")
    tc_existing_400_220: Optional[str] = Field(None, alias="Transformation Capacity (MVA) > Existing > 400/220kV or 400/132kV")
    
    tc_ui_765_400: Optional[str] = Field(None, alias="Transformation Capacity (MVA) > Under Implementation > 765/400kV")
    tc_ui_400_220: Optional[str] = Field(None, alias="Transformation Capacity (MVA) > Under Implementation > 400/220kV")
    
    tc_planned_765_400: Optional[str] = Field(None, alias="Transformation Capacity (MVA) > Planned > 765/400kV")
    tc_planned_400_220: Optional[str] = Field(None, alias="Transformation Capacity (MVA) > Planned > 400/220kV")
    
    allocated_mw: Optional[str] = Field(None, alias="Capacity Allocated (MW)")
    
    additional_margin_existing_220kv: Optional[str] = Field(None, alias="Additional Margin on existing / UC system > 220kV level")
    additional_margin_existing_400kv: Optional[str] = Field(None, alias="Additional Margin on existing / UC system > 400kV level")
    
    additional_margin_ict_220kv: Optional[str] = Field(None, alias="Additional Margin with ICT Augmentation > 220kV level")
    additional_margin_ict_400kv: Optional[str] = Field(None, alias="Additional Margin with ICT Augmentation > 400kV level")
    
    no_of_trfs_required: Optional[str] = Field(None, alias="No. of Trfs required for RE integration")
    remarks: Optional[str] = Field(None, alias="Remarks")

    class Config:
        populate_by_name = True


class FlatProposedRESubstationMarginResult(BaseModel):
    as_on_date: Optional[str] = Field(None, alias="As On Date")
    records: list[FlatProposedRESubstationMarginRecord] = Field(default_factory=list)

    class Config:
        populate_by_name = True


class FlatRESubstationMarginRecord(BaseModel):
    """Flat extraction model for RE Substations."""
    region: Optional[str] = Field(None, alias="Region", description="Region name header (e.g. Northern Region) — carry forward.")
    category: Optional[str] = Field(None, alias="Category", description="Category header (e.g. Existing RE Pooling Stations) — carry forward.")
    pooling_station: Optional[str] = Field(None, alias="Pooling Station")
    state: Optional[str] = Field(None, alias="State")
    
    re_potential_pot: Optional[str] = Field(None, alias="RE Potential (MW) > RE Potential [A]")
    re_potential_bess: Optional[str] = Field(None, alias="RE Potential (MW) > BESS [B]")
    re_potential_evac: Optional[str] = Field(None, alias="RE Potential (MW) > S/s Evacuation Capacity [A-B]")
    
    expected_cod: Optional[str] = Field(None, alias="Expected CoD")
    
    conn_granted_220: Optional[str] = Field(None, alias="Connectivity Granted / Agreed (MW) > 220kV")
    conn_granted_400: Optional[str] = Field(None, alias="Connectivity Granted / Agreed (MW) > 400kV")
    conn_granted_total: Optional[str] = Field(None, alias="Connectivity Granted / Agreed (MW) > Total")
    
    conn_up_220: Optional[str] = Field(None, alias="Connectivity Under Process (MW) > 220kV")
    conn_up_400: Optional[str] = Field(None, alias="Connectivity Under Process (MW) > 400kV")
    conn_up_total: Optional[str] = Field(None, alias="Connectivity Under Process (MW) > Total")
    
    margin_conn_220: Optional[str] = Field(None, alias="Margin for Connectivity (MW) > 220kV")
    margin_conn_400: Optional[str] = Field(None, alias="Margin for Connectivity (MW) > 400kV")
    margin_conn_total: Optional[str] = Field(None, alias="Margin for Connectivity (MW) > Total")
    
    margin_ict_220: Optional[str] = Field(None, alias="Additional Margin for Connectivity requiring ICT Augmentation / additional Tr. System (MW) > 220kV")
    margin_ict_400: Optional[str] = Field(None, alias="Additional Margin for Connectivity requiring ICT Augmentation / additional Tr. System (MW) > 400kV")
    margin_ict_total: Optional[str] = Field(None, alias="Additional Margin for Connectivity requiring ICT Augmentation / additional Tr. System (MW) > Total")
    
    gna_effectiveness: Optional[str] = Field(None, alias="Effectiveness of GNA for Capacity mentioned under 'Margin for Connectivity'")

    class Config:
        populate_by_name = True


class FlatRESubstationMarginResult(BaseModel):
    as_on_date: Optional[str] = Field(None, alias="As On Date")
    records: list[FlatRESubstationMarginRecord] = Field(default_factory=list)

    class Config:
        populate_by_name = True

