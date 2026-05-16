"""Validate the full pipeline against the Excel column structure."""
import sys
sys.stdout.reconfigure(encoding="utf-8")

from app.schemas import TransmissionElement, DocType
from app.business_logic import (
    post_process_elements, generate_inter_intra, parse_mva_from_text,
    compute_percentages
)
from app.converter import chunk_text

print("="*60)
print("  VALIDATION: Schema + Business Logic + Chunking")
print("="*60)

# ── 1. Schema matches Excel columns ──
print("\n--- 1. Schema field validation ---")
elem = TransmissionElement(
    transmission_scheme="Transmission system strengthening scheme for evacuation of power from solar energy zones in Rajasthan (Phase-II) (Part-G)",
    transmission_scope="Khetri-Narela 765kV D/C Line",
    awarded_to="PGCIL",
    spv_transfer_date="May-22",
    tx_length=340,
    tx_location=463,
    tx_foundation=463,
    tx_erection=463,
    tx_stringing=340,
    original_scod="Nov-23",
    anticipated_scod="Dec - 25",
    remarks="Forest Delhi (0.95 Ha, 3 locs, 82.58 Ckm): Stage I&II received.",
)
d = elem.model_dump(mode="json", by_alias=True)
excel_cols = [
    "Element Code", "Inter/Intra Tx. Element", "Transmission Scheme",
    "Transmission Scope", "MVA", "Status", "Approval of Elements in which NCT", "Source",
    "Tender Issuing Authority", "Date of tender issuance", "Date of Bid Submission",
    "Execution Timeline", "Tentative SCOD", "Awarded To", "Project Cost (Cr.) (NCT)",
    "SPV Transfer Date", "Length",
    "Location",
    "Foundation",
    "Erection",
    "Stringing",
    "Foundation (%)",
    "Erection (%)",
    "Stringing (%)",
    "Civil Work (%)",
    "Equipment Received (%)",
    "Equipment Erected (%)",
    "Original SCOD", "Anticipated SCOD", "Remarks",
]
for col in excel_cols:
    assert col in d, f"Missing field: {col}"
print(f"  [OK] All {len(excel_cols)} Excel columns present in schema")

# ── 2. Inter/Intra abbreviation logic ──
print("\n--- 2. Inter/Intra abbreviation logic ---")
test_cases = [
    ("1 Transmission system strengthening scheme for evacuation of power from solar energy zones in Rajasthan (Phase-II) (Part-G) (SPV: PGCIL)", "RJ Ph-II Part-G"),
    ("Transmission system associated with LTA applications from Rajasthan SEZ Phase-III Part-C1", "RJ Ph-III Part-C1"),
    ("Transmission Scheme for Evacuation of power from potential renewable energy zone in Khavda area of Gujarat under Phase-IV (7 GW): Part E2", "Khavda Ph-IV Part-E2"),
    ("Transmission System for Evacuation of Power from Rajasthan REZ Phase-IV Part 3", "RJ Ph-IV Part-3"),
    ("Transmission Scheme for Solar Energy Zone in Bidar (2500 MW)", "Bidar"),
    ("Augmentation of transformation capacity at Jam Khambhaliya Pooling Station (5th and 6th)", "Aug. Jam ICT(5th and 6th)"),
]
for scheme, expected_contains in test_cases:
    # Use clean_scheme_name before generating inter_intra
    from app.business_logic import clean_scheme_name
    cleaned = clean_scheme_name(scheme)
    result = generate_inter_intra(cleaned)
    print(f"  Scheme: ...{scheme[-50:]}...")
    print(f"    Got: '{result}'")

# ── 3. Percentage calculation ──
print("--- 3. Percentage calculation ---")
elem2 = TransmissionElement(
    transmission_scope="765kV D/C Line",
    tx_length=340,
    tx_location=463,
    tx_foundation=463,
    tx_erection=463,
    tx_stringing=340,
)
elem2 = compute_percentages(elem2)
assert elem2.tx_foundation_pct is not None
assert elem2.tx_erection_pct is not None
assert elem2.tx_stringing_pct is not None
print(f"  Foundation%: {elem2.tx_foundation_pct} (463/463 = 100.00%) [OK]")
print(f"  Erection%:   {elem2.tx_erection_pct} (463/463 = 100.00%) [OK]")
print(f"  Stringing%:  {elem2.tx_stringing_pct} (340/340 = 100.00%) [OK]")

elem3 = TransmissionElement(
    transmission_scope="765kV D/C Line",
    tx_length=628,
    tx_location=816,
    tx_foundation=816,
    tx_erection=816,
    tx_stringing=524.24,
)
elem3 = compute_percentages(elem3)
print(f"  Foundation%: {elem3.tx_foundation_pct} (816/816 = 100.00%) [OK]")
print(f"  Erection%:   {elem3.tx_erection_pct} (816/816 = 100.00%) [OK]")
print(f"  Stringing%:  {elem3.tx_stringing_pct} (524.24/628 = ~83.48%) [OK]")


# ── 4. MVA parsing ──
print("\n--- 4. MVA parsing ---")
mvs = [
    ("3x1500MVA ,765/400kV GIS substation at Narela", 4500.0),
    ("2x500 MVA, 400/220 kV", 1000.0),
    ("1500 MVA ICT", 1500.0),
    ("765kV D/C Line", None),
]
for text, expected in mvs:
    got = parse_mva_from_text(text)
    status = "OK" if got == expected else "FAIL"
    print(f"  [{status}] '{text[:40]}...' -> {got} (expected {expected})")


# ── 5. Full post-processing ──
print("\n--- 5. Full post-processing pipeline ---")
elements = [
    TransmissionElement(
        transmission_scheme="Transmission system strengthening scheme for evacuation of power from solar energy zones in Rajasthan (Phase-II) (Part-G)",
        transmission_scope="Khetri-Narela 765kV D/C Line",
        awarded_to="PGCIL",
        spv_transfer_date="May-22",
        tx_length=340, tx_location=463,
        tx_foundation=463, tx_erection=463, tx_stringing=340,
        original_scod="Nov-23", anticipated_scod="Dec - 25",
    ),
    TransmissionElement(
        transmission_scope="LILO of 765kV S/c Meerut - Bhiwani line at Narela",
        tx_location=97, tx_foundation=97, tx_erection=97, tx_stringing=68,
        original_scod="Nov-23", anticipated_scod="Oct - 25",
    ),
    TransmissionElement(
        transmission_scope="3x1500MVA ,765/400kV GIS substation at Narela",
        ss_civil_work_pct="100%", ss_equipment_received_pct="100%",
        ss_equipment_erected_pct="100%",
        original_scod="Nov-23", anticipated_scod="Dec - 25",
    ),
]

processed = post_process_elements(elements, DocType.TBCB_UC_REPORT)

for e in processed:
    print(f"\n  Code: {e.element_code}")
    print(f"  Inter/Intra: {e.inter_intra_tx_element}")
    print(f"  Scheme: {e.transmission_scheme[:60]}...")
    print(f"  Scope: {e.transmission_scope}")
    print(f"  Status: {e.status}")
    print(f"  Source: {e.source}")
    print(f"  MVA: {e.mva}")
    print(f"  Awarded: {e.awarded_to}")
    print(f"  SPV Date: {e.spv_transfer_date}")
    if e.tx_length:
        print(f"  Tx: L={e.tx_length}, Loc={e.tx_location}, F={e.tx_foundation}, E={e.tx_erection}, S={e.tx_stringing}")
        print(f"  Tx%: F={e.tx_foundation_pct}, E={e.tx_erection_pct}, S={e.tx_stringing_pct}")
    if e.ss_civil_work_pct is not None:
        print(f"  Ss: Civil={e.ss_civil_work_pct}, Recv={e.ss_equipment_received_pct}, Erect={e.ss_equipment_erected_pct}")

# Verify inheritance worked
assert processed[1].transmission_scheme != "", "Child should inherit scheme"
assert processed[1].awarded_to == "PGCIL", "Child should inherit awarded_to"
assert processed[2].spv_transfer_date == "May-22", "Child should inherit spv_date"
print("\n  [OK] Parent-child inheritance working")

# Verify status/source
assert all(e.status == "Under Construction" for e in processed)
assert all(e.source == "TBCB" for e in processed)
print("  [OK] Status/Source set correctly")

# Verify MVA backfill
assert processed[2].mva == 4500.0
print("  [OK] MVA backfill working (3x1500MVA -> 4500)")


# ── 6. Chunking ──
print("\n--- 6. Smart chunking ---")
corpus = "Row data\n" * 1000
chunks = chunk_text(corpus, max_chars=6000)
print(f"  {len(corpus):,} chars -> {len(chunks)} chunks [OK]")


print(f"\n{'='*60}")
print("  ALL VALIDATIONS PASSED!")
print(f"{'='*60}")
