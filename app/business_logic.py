"""Post-processing business logic for extracted transmission elements.

Deterministic Python logic applied AFTER LLM extraction to ensure
data consistency the LLM cannot guarantee:

1. Element Code     - Unique, deterministic hash-based ID
2. Inter/Intra      - Abbreviation from scheme name (Phase/Part/Region logic)
3. Status           - Forced from doc_type
4. Source           - Forced from doc_type
5. MVA              - Parsed from transmission_scope text
6. Parent-child     - Scheme name inherited to child rows
7. Percentage Calc  - Foundation%=Foundation/Location, etc.
"""

from __future__ import annotations

import hashlib
import re
from typing import Optional

from app.schemas import DocType, TransmissionElement


# ── 1. Element Code Generation ─────────────────────────────────────────


def generate_element_code(
    element: TransmissionElement,
    index: int,
    doc_type: DocType,
) -> str:
    """Generate a unique, deterministic element code (Col B).

    Strategy:
    - If the LLM already extracted a valid EL-XXXXX code, keep it.
    - Otherwise, generate from hash of (scheme + scope + index).

    Returns:
        Element code like ``EL-89310`` or ``EL-TBUC-0042-A3F2``.
    """
    existing = (element.element_code or "").strip()

    # Keep valid existing codes
    if existing and re.match(r"^EL-[A-Z0-9]{3,6}$", existing):
        return existing

    # Generate deterministic code
    prefix_map = {
        DocType.TBCB_UC_REPORT: "TBUC",
        DocType.TBCB_COMM_REPORT: "TBCM",
        DocType.RTM_UC_REPORT: "RTM",
        DocType.NCT_REPORT: "NCT",
        DocType.GENERAL: "GEN",
    }
    prefix = prefix_map.get(doc_type, "GEN")

    content = f"{element.transmission_scheme}|{element.transmission_scope}|{index}"
    short_hash = hashlib.md5(content.encode()).hexdigest()[:5].upper()

    return f"EL-{short_hash}"


# ── 2. Inter/Intra Tx Element (Col C) — Abbreviation Logic ────────────
#
# Rules from test.txt:
#   IF phase and part available:
#     "... Rajasthan ... Phase-III Part-C1" → "RJ Ph-III Part-C"
#     "... Khavda ... Phase-IV ... Part E2" → "Kh Ph-IV Part-E2"
#   IF phase/part NOT available:
#     Region names → abbreviations: Western Region → WR, etc.
#     State/location names used as-is: "Bidar", "Ananthpuram & Kurnool"
#
# Col D (transmission_scheme) = full name excluding SPV.
# Col C = abbreviated short name.

_STATE_ABBREVS = {
    "rajasthan": "RJ",
    "gujarat": "GJ",
    "karnataka": "KA",
    "andhra pradesh": "AP",
    "telangana": "TS",
    "tamil nadu": "TN",
    "maharashtra": "MH",
    "madhya pradesh": "MP",
    "uttar pradesh": "UP",
    "haryana": "HR",
    "punjab": "PB",
    "chhattisgarh": "CG",
    "odisha": "OD",
    "jharkhand": "JH",
    "bihar": "BR",
    "west bengal": "WB",
    "kerala": "KL",
    "assam": "AS",
    "arunachal pradesh": "AR",
}

_REGION_ABBREVS = {
    "western region": "WR",
    "eastern region": "ER",
    "northern region": "NR",
    "southern region": "SR",
    "north eastern region": "NER",
    "western & southern": "WR & SR",
    "northern & western": "NR & WR",
}

_LOCATION_ABBREVS = {
    "khavda": "Khavda",
    "bhadla": "Bhadla",
    "bikaner": "Bikaner",
    "jaisalmer": "Jaisalmer",
    "barmer": "Barmer",
    "bidar": "Bidar",
    "ananthpuram": "Ananthpuram",
    "ananthapur": "Ananthapur",
    "kurnool": "Kurnool",
    "koppal": "Koppal",
    "gadag": "Gadag",
    "jam": "Jam",
    "narela": "Narela",
    "sikar": "Sikar",
}


def generate_inter_intra(scheme: str) -> str:
    """Generate abbreviated Inter/Intra Tx. Element (Col C) from scheme name.

    Priority:
      1. If Phase + Part found → "XX Ph-N Part-P"
      2. If Augmentation found → "Aug. Location ICT(...)"
      3. If location/state + Phase found → "XX Ph-N"
      4. If region found → "WR" / "SR" / "WR & SR"
      5. If location found → Location name(s)
      6. Fallback → first 30 chars
    """
    if not scheme:
        return ""

    name = scheme.strip()

    # ── Try Phase + Part ──
    # Match patterns like "Phase-III", "Phase-IV", "Phase IV"
    phase_match = re.search(
        r"Phase[\s\-]*([IVXLC]+|\d+)",
        name, re.IGNORECASE,
    )
    part_match = re.search(
        r"Part[\s\-]*([A-Z0-9]+)",
        name, re.IGNORECASE,
    )

    if phase_match:
        phase_num = phase_match.group(1).upper()
        phase_str = f"Ph-{phase_num}"

        # Find location/state prefix
        prefix = _find_prefix(name)

        if part_match:
            part_id = part_match.group(1)
            return f"{prefix} {phase_str} Part-{part_id}".strip()
        else:
            return f"{prefix} {phase_str}".strip()

    # ── Augmentation ──
    aug_match = re.search(r"augmentation", name, re.IGNORECASE)
    if aug_match:
        loc = _find_location(name)
        # Check for ICT numbering
        ict_match = re.search(r"\((\d+(?:st|nd|rd|th).*?)\)", name, re.IGNORECASE)
        if ict_match and loc:
            return f"Aug. {loc} ICT({ict_match.group(1)})"
        elif loc:
            return f"Aug. {loc}"

    # ── Strengthening ──
    str_match = re.search(r"strengthening", name, re.IGNORECASE)
    if str_match:
        locs = _find_all_locations(name)
        if locs:
            return f"Str. {'& '.join(locs)}"

    # ── Dynamic Reactive / STATCOM ──
    if re.search(r"dynamic reactive|statcom|reactor", name, re.IGNORECASE):
        locs = _find_all_locations(name)
        if locs:
            return " & ".join(locs)

    # ── Region abbreviation ──
    for region_name, abbrev in _REGION_ABBREVS.items():
        if region_name.lower() in name.lower():
            return abbrev

    # ── Location names ──
    locs = _find_all_locations(name)
    if locs:
        return " & ".join(locs)

    # Fallback
    return name[:30]


def _find_prefix(scheme: str) -> str:
    """Find the state/location abbreviation prefix for a scheme name."""
    name_lower = scheme.lower()

    # Check Khavda specially (multiple variants)
    if "khavda" in name_lower:
        # Check for KPS
        kps_match = re.search(r"kps[\s\-]*(\d+|[ivx]+)", name_lower)
        if kps_match:
            return f"Khavda & KPS-{kps_match.group(1).upper()}"
        return "Khavda"

    # Check states
    for state, abbrev in _STATE_ABBREVS.items():
        if state in name_lower:
            # Check for additional location context
            locs = _find_all_locations(scheme)
            if locs:
                return f"{abbrev} & {' & '.join(locs)}"
            return abbrev

    # Check locations
    loc = _find_location(scheme)
    if loc:
        return loc

    return ""


def _find_location(scheme: str) -> str:
    """Find the first significant location name in a scheme."""
    name_lower = scheme.lower()
    for loc_lower, loc_name in _LOCATION_ABBREVS.items():
        if loc_lower in name_lower:
            return loc_name
    return ""


def _find_all_locations(scheme: str) -> list[str]:
    """Find all significant location names in a scheme."""
    name_lower = scheme.lower()
    found = []
    for loc_lower, loc_name in _LOCATION_ABBREVS.items():
        if loc_lower in name_lower and loc_name not in found:
            found.append(loc_name)
    return found


# ── 3. Status — From Document Type ────────────────────────────────────


def get_status_for_doc_type(doc_type: DocType) -> str:
    """Return the correct status string (Col G)."""
    if doc_type in (DocType.TBCB_UC_REPORT, DocType.RTM_UC_REPORT, DocType.NCT_REPORT):
        return "Under Construction"
    elif doc_type == DocType.TBCB_COMM_REPORT:
        return "Commissioned"
    return "Unknown"


# ── 4. Source — From Document Type ─────────────────────────────────────


def get_source_for_doc_type(doc_type: DocType) -> str:
    """Return the source identifier (Col I)."""
    source_map = {
        DocType.TBCB_UC_REPORT: "TBCB",
        DocType.TBCB_COMM_REPORT: "TBCB Com",
        DocType.RTM_UC_REPORT: "RTM",
        DocType.NCT_REPORT: "NCT",
        DocType.GENERAL: "General",
    }
    return source_map.get(doc_type, "")


# ── 5. MVA Parsing ────────────────────────────────────────────────────

_MVA_PATTERN = re.compile(
    r"(\d+)\s*[xX×]\s*(\d+)\s*MVA(?!r)",
    re.IGNORECASE,
)
_MVA_SINGLE = re.compile(
    r"(\d+)\s*MVA(?!r)",
    re.IGNORECASE,
)


def parse_mva_from_text(text: str) -> Optional[float]:
    """Parse total MVA from scope text.

    '3x1500MVA' → 4500.0, '2x500 MVA' → 1000.0, '1500 MVA' → 1500.0
    """
    if not text:
        return None

    # NxM MVA patterns
    multiplied = _MVA_PATTERN.findall(text)
    if multiplied:
        total = 0.0
        for count_str, mva_str in multiplied:
            total += int(count_str) * int(mva_str)
        return total

    # Single MVA value
    single = _MVA_SINGLE.findall(text)
    if single:
        return float(max(int(v) for v in single))

    return None


# ── 6. Parent-Child Scheme Inheritance ─────────────────────────────────


def clean_scheme_name(scheme: str) -> str:
    """Strip leading serial numbers and trailing SPV references."""
    if not scheme:
        return scheme
    
    # Remove leading serial numbers like "1 ", "1.", "2 - "
    cleaned = re.sub(r"^\d+\s*[\.\-\)]?\s*", "", scheme.strip())
    
    # Remove trailing "(SPV: ...)" blocks
    cleaned = re.sub(r"\s*\(SPV:.*?\)$", "", cleaned, flags=re.IGNORECASE)
    
    return cleaned.strip()


def inherit_scheme_to_children(
    elements: list[TransmissionElement],
) -> list[TransmissionElement]:
    """Fill empty scheme names from the nearest parent row above.

    Also inherits: awarded_to, spv_transfer_date.
    """
    current_scheme = ""
    current_awarded_to = ""
    current_spv_date = ""

    for elem in elements:
        if elem.transmission_scheme and elem.transmission_scheme.strip():
            current_scheme = clean_scheme_name(elem.transmission_scheme)
            elem.transmission_scheme = current_scheme
            if elem.awarded_to:
                current_awarded_to = elem.awarded_to
            if elem.spv_transfer_date:
                current_spv_date = elem.spv_transfer_date
        else:
            if current_scheme:
                elem.transmission_scheme = current_scheme
            if not elem.awarded_to and current_awarded_to:
                elem.awarded_to = current_awarded_to
            if not elem.spv_transfer_date and current_spv_date:
                elem.spv_transfer_date = current_spv_date

    return elements


# ── 7. Percentage Calculations ─────────────────────────────────────────


def compute_percentages(element: TransmissionElement) -> TransmissionElement:
    """Compute physical progress percentages (Cols W, X, Y) and format as strings.

    Formulas:
      Foundation (%) = Foundation / Location
      Erection (%)   = Erection / Location
      Stringing (%)  = Stringing / Length
    """
    # Foundation % = Foundation / Location
    if element.tx_foundation is not None and element.tx_location and element.tx_location > 0:
        val = element.tx_foundation / element.tx_location
        element.tx_foundation_pct = f"{val * 100:.2f}%"

    # Erection % = Erection / Location
    if element.tx_erection is not None and element.tx_location and element.tx_location > 0:
        val = element.tx_erection / element.tx_location
        element.tx_erection_pct = f"{val * 100:.2f}%"

    # Stringing % = Stringing / Length
    if element.tx_stringing is not None and element.tx_length and element.tx_length > 0:
        val = element.tx_stringing / element.tx_length
        element.tx_stringing_pct = f"{val * 100:.2f}%"

    return element


# ── 8. MVA Backfill ────────────────────────────────────────────────────


def backfill_mva(element: TransmissionElement) -> TransmissionElement:
    """Backfill MVA from transmission_scope if LLM didn't compute it."""
    if element.mva is None or element.mva == 0:
        parsed = parse_mva_from_text(element.transmission_scope)
        if parsed:
            element.mva = parsed
    return element


# ── Master Post-Processing Pipeline ───────────────────────────────────


def post_process_elements(
    elements: list[TransmissionElement],
    doc_type: DocType,
) -> list[TransmissionElement]:
    """Apply ALL business logic rules to extracted elements.

    Order matters:
    1. Inherit scheme names to children
    2. Per-element:
       a. Generate element code (Col B)
       b. Generate inter_intra abbreviation (Col C)
       c. Force status from doc_type (Col G)
       d. Force source from doc_type (Col I)
       e. Backfill MVA from scope (Col F)
       f. Compute percentages (Cols W, X, Y)
    """
    if not elements:
        return elements

    # Step 1: Parent-child inheritance
    elements = inherit_scheme_to_children(elements)

    status = get_status_for_doc_type(doc_type)
    source = get_source_for_doc_type(doc_type)

    for i, elem in enumerate(elements, 1):
        # a. Element code
        elem.element_code = generate_element_code(elem, i, doc_type)

        # b. Inter/Intra abbreviation from scheme
        elem.inter_intra_tx_element = generate_inter_intra(
            elem.transmission_scheme
        )

        # c. Status
        elem.status = status

        # d. Source
        elem.source = source

        # e. MVA backfill
        elem = backfill_mva(elem)

        # f. Percentage calculations
        elem = compute_percentages(elem)

    print(f"[business_logic] Post-processed {len(elements)} elements")
    print(f"[business_logic]   Status: {status}, Source: {source}")

    with_mva = sum(1 for e in elements if e.mva and e.mva > 0)
    with_pct = sum(1 for e in elements if e.tx_foundation_pct is not None or e.ss_civil_work_pct is not None)
    print(f"[business_logic]   Elements with MVA: {with_mva}/{len(elements)}")
    print(f"[business_logic]   Elements with progress %: {with_pct}/{len(elements)}")

    return elements
