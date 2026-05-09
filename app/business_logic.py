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
8. Tentative SCOD   - Force-cleared (not discussed yet)
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
    """Generate a unique, deterministic element code (Col B)."""
    existing = (element.element_code or "").strip()

    # Keep valid existing codes
    if existing and re.match(r"^EL-[A-Z0-9]{3,6}$", existing):
        return existing

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
# Rules (from Excel reference):
#   IF phase and part available:
#     "... Rajasthan ... Phase-III Part-C1" → "RJ Ph-III Part C1"
#     "... Rajasthan SEZ Phase-III Part-C1" → "RJ Ph-III Part C1 SEZ"
#     "... Khavda ... Phase-IV ... Part E2" → "GJ & Khavda Ph-IV Part E2"
#     "... Rajasthan REZ Ph-IV (Part-1) (Bikaner Complex)" → "RJ & Bikaner Ph-IV Part 1"
#   IF phase/part NOT available:
#     Region names → abbreviations: Western Region → WR, etc.
#     Location names used as-is: "Bidar", "Davanagere & Chitradurga & Bellary"
#   Augmentation: "Aug. Jam ICT(...)" / "Aug. Bidar ICT(...)"
#   Strengthening: "Str. Bhadla-III & Bikaner-III"
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

# Locations with optional suffixes like -I, -II, -III, -IV, -2, -3, etc.
# We search for these in the scheme text and preserve any suffix.
_LOCATION_KEYWORDS = [
    "khavda", "bhadla", "bikaner", "jaisalmer", "barmer",
    "bidar", "ananthpuram", "ananthapur", "kurnool", "koppal",
    "gadag", "jam", "narela", "sikar", "fatehgarh", "davanagere",
    "chitradurga", "bellary", "kudankulam", "sirohi", "nagaur",
    "banaskantha", "raghanesda", "neemrana", "ramgarh", "beawar",
    "kps", "unit",
]

# Pre-compiled regex for finding location + optional suffix
_LOC_SUFFIX_RE = re.compile(
    r"\b("
    + "|".join(re.escape(loc) for loc in _LOCATION_KEYWORDS)
    + r")(?:[- ]*((?:I{1,3}V?|IV|V|VI{0,3}|\d+)))?(?:\s*PS)?",
    re.IGNORECASE,
)


def _extract_locations_with_suffix(text: str) -> list[str]:
    """Extract location names with their roman/numeric suffixes from text.

    Examples:
        "Bhadla-III and Bikaner-III" → ["Bhadla-III", "Bikaner-III"]
        "Koppal-II (Phase-A & B) and Gadag-II" → ["Koppal-II", "Gadag-II"]
        "Bikaner Complex" → ["Bikaner"]
        "KPS1 and KPS3" → ["KPS-I", "KPS-III"]
    """
    found = []
    seen_lower = set()
    for m in _LOC_SUFFIX_RE.finditer(text):
        base = m.group(1)
        suffix = m.group(2) or ""

        # Normalize: capitalize base
        base_cap = base.capitalize()
        if base.lower() == "kps":
            base_cap = "KPS"

        # Normalize suffix to Roman numerals for small numbers
        if suffix:
            suffix_upper = suffix.upper()
            # If it's a digit, convert small ones to roman
            if suffix.isdigit():
                digit_to_roman = {"1": "I", "2": "II", "3": "III",
                                  "4": "IV", "5": "V", "6": "VI"}
                suffix_upper = digit_to_roman.get(suffix, suffix)
            loc_name = f"{base_cap}-{suffix_upper}"
        else:
            loc_name = base_cap

        key = loc_name.lower()
        if key not in seen_lower:
            seen_lower.add(key)
            found.append(loc_name)
    return found


def _extract_slash_locations(text: str) -> list[str]:
    """Extract slash-separated location pairs like 'Jaisalmer/Barmer'."""
    matches = re.findall(
        r"\b([A-Z][a-z]+)\s*/\s*([A-Z][a-z]+)\b", text
    )
    results = []
    for a, b in matches:
        # Only include if both are known locations
        a_known = a.lower() in [l for l in _LOCATION_KEYWORDS]
        b_known = b.lower() in [l for l in _LOCATION_KEYWORDS]
        if a_known or b_known:
            results.append(f"{a}/{b}")
    return results


def _find_state(text: str) -> str:
    """Find state abbreviation from text. Returns empty string if none."""
    text_lower = text.lower()
    for state, abbrev in _STATE_ABBREVS.items():
        if state in text_lower:
            return abbrev
    return ""


def generate_inter_intra(scheme: str) -> str:
    """Generate abbreviated Inter/Intra Tx. Element (Col C) from scheme name.

    Follows the Excel reference naming convention.
    PRIORITY ORDER matters — Phase/Part is checked FIRST so that schemes
    containing words like "strengthening" but also having Phase/Part info
    are correctly handled (e.g. "strengthening scheme... under Phase-II Part G").

      1. Phase + Part → "{prefix} Ph-X Part Y [SEZ]"  (Part Y, not Part-Y)
      2. Phase only → "{prefix} Ph-X"
      3. Part only (no Phase) → "{prefix} Part Y [SEZ]"
      4. Augmentation → "Aug. {location} [ICT({n})]"
      5. Strengthening → "Str. {loc1} & {loc2}"
      6. Dynamic Reactive / STATCOM → location-based
      7. Region → "WR" / "SR" / "WR & SR"
      8. Location names only → "Loc1 & Loc2"
      9. Fallback → first 30 chars
    """
    if not scheme:
        return ""

    name = scheme.strip().rstrip(",").strip()

    # ── 1. Phase + Part (main case — checked FIRST) ──
    # Match: "Phase-III", "Phase IV", "phase- II", "Ph-IV", "PH-IV"
    phase_match = re.search(
        r"(?:Phase|Ph)[\s\-]*([IVXLC]+|\d+)", name, re.IGNORECASE
    )
    # Part patterns — handle multiple formats:
    #   "Part-C1", "Part G", "Part- E2", ": PART-A", "(Part-1: 6GW)", "(PART-3: 6GW)"
    part_match = re.search(
        r"(?::\s*)?(?:\(?\s*)Part[\s\-]*([A-Z]\d*|\d+)(?:\s*[:\)])?",
        name, re.IGNORECASE,
    )

    if phase_match:
        phase_num = phase_match.group(1).upper()
        digit_to_roman = {"1": "I", "2": "II", "3": "III",
                          "4": "IV", "5": "V", "6": "VI"}
        phase_roman = digit_to_roman.get(phase_num, phase_num)
        phase_str = f"Ph-{phase_roman}"

        # Build prefix: state + significant locations
        prefix = _build_prefix(name)

        # Detect SEZ keyword
        has_sez = bool(re.search(r"\bSEZ\b", name))

        if part_match:
            part_id = part_match.group(1).upper()
            result = f"{prefix} {phase_str} Part {part_id}".strip()
        else:
            result = f"{prefix} {phase_str}".strip()

        if has_sez:
            result += " SEZ"

        return result

    # ── 2. Part only (no explicit Phase keyword) ──
    if part_match:
        part_id = part_match.group(1).upper()
        prefix = _build_prefix(name)
        has_sez = bool(re.search(r"\bSEZ\b", name))
        result = f"{prefix} Part {part_id}".strip()
        if has_sez:
            result += " SEZ"
        return result

    # ── 3. Augmentation (only if no phase/part) ──
    if re.search(r"augmentation", name, re.IGNORECASE):
        return _handle_augmentation(name)

    # ── 4. Strengthening (only if no phase/part) ──
    if re.search(r"strengthening", name, re.IGNORECASE):
        return _handle_strengthening(name)

    # ── 5. Dynamic Reactive / Compensation / STATCOM ──
    if re.search(r"dynamic reactive|compensation|statcom|reactor",
                 name, re.IGNORECASE):
        locs = _extract_locations_with_suffix(name)
        if locs:
            return " & ".join(locs)

    # ── 6. Region abbreviation ──
    name_lower = name.lower()
    for region_name, abbrev in _REGION_ABBREVS.items():
        if region_name in name_lower:
            return abbrev

    # ── 7. Location names (no phase/part) ──
    # For location-only schemes, do NOT prepend state abbreviation
    # (e.g. "Bidar (2500 MW), Karnataka" → "Bidar", not "KA & Bidar")
    slash_locs = _extract_slash_locations(name)
    if slash_locs:
        return " & ".join(slash_locs)

    locs = _extract_locations_with_suffix(name)
    if locs:
        return " & ".join(locs)

    # ── 8. Fallback ──
    return name[:30]


def _build_prefix(scheme: str) -> str:
    """Build the state + location prefix for a scheme name.

    Examples:
        "... Rajasthan REZ Ph-IV (Part-1) (Bikaner Complex)"  → "RJ & Bikaner"
        "... Rajasthan (20 GW) under Phase-III Part H"         → "RJ"
        "... Khavda area of Gujarat under Phase-IV"            → "GJ & Khavda"
        "... Khavda RE Park under Phase-III Part B"            → "Khavda"
        "... Koppal-II ... and Gadag-II ... in Karnataka"      → "Koppal-II & Gadag-II"
    """
    state = _find_state(scheme)
    name_lower = scheme.lower()

    # Extract significant locations (excluding generic state-level references)
    locs = _extract_locations_with_suffix(scheme)

    # Check for slash-separated locations (Jaisalmer/Barmer, Sirohi/Nagaur)
    slash_locs = _extract_slash_locations(scheme)

    # Build location string
    loc_parts = []
    if slash_locs:
        loc_parts = slash_locs
    elif locs:
        loc_parts = locs

    # Filter out "Unit" and similar noise words when alone
    if len(loc_parts) == 1 and loc_parts[0].lower() == "unit":
        loc_parts = []

    if state and loc_parts:
        return f"{state} & {' & '.join(loc_parts)}"
    elif state:
        return state
    elif loc_parts:
        return " & ".join(loc_parts)
    return ""


def _handle_augmentation(name: str) -> str:
    """Handle augmentation scheme naming: Aug. {location} [ICT({n})]."""
    locs = _extract_locations_with_suffix(name)
    loc_str = " & ".join(locs) if locs else ""

    # Try to find ICT ordinal patterns like "(5th and 6th)", "(4th)"
    ict_match = re.search(
        r"\((\d+(?:st|nd|rd|th)(?:\s*(?:and|,|&)\s*\d+(?:st|nd|rd|th))*)\)",
        name, re.IGNORECASE,
    )
    if ict_match and loc_str:
        return f"Aug. {loc_str} ICT({ict_match.group(1)})"
    elif loc_str:
        return f"Aug. {loc_str}"
    return "Aug."


def _handle_strengthening(name: str) -> str:
    """Handle strengthening scheme naming: Str. {loc1} & {loc2}."""
    locs = _extract_locations_with_suffix(name)
    if locs:
        return f"Str. {' & '.join(locs)}"
    return "Str."


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
    """Strip leading serial numbers, trailing SPV references, and trailing commas."""
    if not scheme:
        return scheme

    # Remove leading serial numbers like "1 ", "1.", "2 - "
    cleaned = re.sub(r"^\d+\s*[.\-)]?\s*", "", scheme.strip())

    # Remove trailing "(SPV: ...)" or "(SPV Name: ...)" blocks
    cleaned = re.sub(r"\s*\(SPV\s*(?:Name)?:.*?\)\s*$", "", cleaned, flags=re.IGNORECASE)

    # Remove trailing commas
    cleaned = cleaned.strip().rstrip(",").strip()

    return cleaned


def inherit_scheme_to_children(
    elements: list[TransmissionElement],
) -> list[TransmissionElement]:
    """Fill empty scheme names from the nearest parent row above.

    Also inherits: awarded_to, spv_transfer_date, tentative_scod.
    """
    current_scheme = ""
    current_awarded_to = ""
    current_spv_date = ""
    current_tentative_scod = ""

    for elem in elements:
        if elem.transmission_scheme and elem.transmission_scheme.strip():
            current_scheme = clean_scheme_name(elem.transmission_scheme)
            elem.transmission_scheme = current_scheme
            if elem.awarded_to:
                current_awarded_to = elem.awarded_to
            if elem.spv_transfer_date:
                current_spv_date = elem.spv_transfer_date
            if elem.tentative_scod:
                current_tentative_scod = elem.tentative_scod
        else:
            if current_scheme:
                elem.transmission_scheme = current_scheme
            if not elem.awarded_to and current_awarded_to:
                elem.awarded_to = current_awarded_to
            if not elem.spv_transfer_date and current_spv_date:
                elem.spv_transfer_date = current_spv_date
            if not elem.tentative_scod and current_tentative_scod:
                elem.tentative_scod = current_tentative_scod

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


# ── 9. SPV Transfer Date Validation ──────────────────────────────────


_DATE_LIKE = re.compile(
    r"^(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s*[-–]\s*\d{2,4}$",
    re.IGNORECASE,
)


def validate_spv_transfer_date(element: TransmissionElement) -> TransmissionElement:
    """Validate SPV Transfer Date looks like a date (MMM-YY), clear if not."""
    val = (element.spv_transfer_date or "").strip()
    if val and not _DATE_LIKE.match(val):
        # Not a valid date (e.g. a number like "473") — clear it
        element.spv_transfer_date = ""
    return element


# ── 11. Detect Misclassified Scopes as Schemes ────────────────────────


_SCOPE_INDICATORS = re.compile(
    r"(?:765\s*kv|400\s*kv|220\s*kv|132\s*kv|d/c|s/c|"
    r"MVA|ICT|STATCOM|substation|s/s|bay|line\b|ckm|"
    r"lilo|establishment of|augmentation by)",
    re.IGNORECASE,
)

_SCHEME_INDICATORS = re.compile(
    r"(?:transmission\s+(?:system|scheme)|evacuation|"
    r"integration|strengthening|associated\s+with\s+LTA|"
    r"renewable\s+energy\s+zone|solar\s+energy\s+zone|REZ|"
    r"under\s+phase)",
    re.IGNORECASE,
)


def fix_misclassified_elements(
    elements: list[TransmissionElement],
) -> list[TransmissionElement]:
    """Fix elements where a scope was misclassified as a scheme (page-break issue).

    When a table breaks across pages, child scope elements may appear at the
    start of a new chunk without their parent scheme. The LLM may then put
    the scope text into the scheme field. This function detects and fixes that.
    """
    current_scheme = ""

    for elem in elements:
        scheme = (elem.transmission_scheme or "").strip()
        scope = (elem.transmission_scope or "").strip()

        if scheme:
            # Check if scheme text looks like a scope (line/substation description)
            looks_like_scope = bool(_SCOPE_INDICATORS.search(scheme))
            looks_like_scheme = bool(_SCHEME_INDICATORS.search(scheme))

            if looks_like_scope and not looks_like_scheme:
                # This is a scope misclassified as scheme
                if not scope:
                    elem.transmission_scope = scheme
                elem.transmission_scheme = current_scheme  # inherit from previous
            else:
                current_scheme = scheme
        else:
            # Empty scheme — will be filled by inheritance
            pass

    return elements


# ── Master Post-Processing Pipeline ───────────────────────────────────


def post_process_elements(
    elements: list[TransmissionElement],
    doc_type: DocType,
) -> list[TransmissionElement]:
    """Apply ALL business logic rules to extracted elements.

    Order matters:
    1. Fix misclassified scopes (page-break issues)
    2. Inherit scheme names to children
    3. Per-element:
       a. Generate element code (Col B)
       b. Generate inter_intra abbreviation (Col C)
       c. Force status from doc_type (Col G)
       d. Force source from doc_type (Col I)
       e. Backfill MVA from scope (Col F)
       f. Compute percentages (Cols W, X, Y)
       g. Clear Tentative SCOD (not discussed yet)
       h. Validate SPV Transfer Date
    """
    if not elements:
        return elements

    # Step 0: Fix misclassified scopes (page-break issues)
    elements = fix_misclassified_elements(elements)

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

        # g. Validate SPV Transfer Date
        elem = validate_spv_transfer_date(elem)

    print(f"[business_logic] Post-processed {len(elements)} elements")
    print(f"[business_logic]   Status: {status}, Source: {source}")

    with_mva = sum(1 for e in elements if e.mva and e.mva > 0)
    with_pct = sum(1 for e in elements if e.tx_foundation_pct is not None or e.ss_civil_work_pct is not None)
    print(f"[business_logic]   Elements with MVA: {with_mva}/{len(elements)}")
    print(f"[business_logic]   Elements with progress %: {with_pct}/{len(elements)}")

    return elements
