"""Integration test: post-processing pipeline."""
from app.business_logic import (
    post_process_elements, fix_misclassified_elements,
    clean_scheme_name, validate_spv_transfer_date,
)
from app.schemas import DocType, TransmissionElement

# Test 1: Tentative SCOD clearing
elem = TransmissionElement(
    transmission_scheme="Transmission system for evacuation of power from REZ in Rajasthan (20 GW) under Phase-III Part H",
    transmission_scope="2x1500MVA, 765/400KV Dausa S/s",
    tentative_scod="Jun - 26",
)
processed = post_process_elements([elem], DocType.TBCB_UC_REPORT)
assert processed[0].tentative_scod == "", f"FAIL: Tentative SCOD should be empty, got: {processed[0].tentative_scod!r}"
print("PASS: Tentative SCOD cleared")

# Test 2: SPV Transfer Date validation
elem2 = TransmissionElement(spv_transfer_date="473")
elem2 = validate_spv_transfer_date(elem2)
assert elem2.spv_transfer_date == "", f"FAIL: '473' is not valid, should be empty, got: {elem2.spv_transfer_date!r}"
print("PASS: Invalid SPV Transfer Date '473' cleared")

elem3 = TransmissionElement(spv_transfer_date="Sep-23")
elem3 = validate_spv_transfer_date(elem3)
assert elem3.spv_transfer_date == "Sep-23", f"FAIL: 'Sep-23' should be kept"
print("PASS: Valid SPV Transfer Date 'Sep-23' kept")

# Test 3: Misclassified scope detection
elems = [
    TransmissionElement(
        transmission_scheme="Transmission System for Evacuation of Power from REZ in Rajasthan (20GW) under Phase-III Part F",
        transmission_scope="2x1500MVA, 765/400KV S/s",
    ),
    TransmissionElement(
        transmission_scheme="fatehgarh3– beawar 765kv d/c line",  # scope misclassified as scheme
        transmission_scope="",
    ),
]
fixed = fix_misclassified_elements(elems)
assert "fatehgarh" in fixed[1].transmission_scope.lower(), f"FAIL: scope not moved, scope={fixed[1].transmission_scope!r}"
assert "Transmission System" in fixed[1].transmission_scheme, f"FAIL: scheme not inherited, scheme={fixed[1].transmission_scheme!r}"
print("PASS: Misclassified scope detected and fixed")

# Test 4: Clean scheme name strips SPV and trailing commas
cleaned = clean_scheme_name("Transmission Scheme for Solar Energy Zone in Bidar (2500 MW), Karnataka (SPV Name: POWERGRID Bidar Transmission Limited)")
assert "SPV" not in cleaned, f"FAIL: SPV not stripped, got: {cleaned!r}"
assert not cleaned.endswith(","), f"FAIL: trailing comma not stripped, got: {cleaned!r}"
print(f"PASS: Scheme cleaned: {cleaned!r}")

# Test 5: Naming convention after post-processing
elem5 = TransmissionElement(
    transmission_scheme="Transmission system strengthening scheme for evacuation of power from solar energy zones in Rajasthan (8.1 GW) under phase- II- Part G",
    transmission_scope="test scope",
)
processed = post_process_elements([elem5], DocType.TBCB_UC_REPORT)
assert processed[0].inter_intra_tx_element == "RJ Ph-II Part G", f"FAIL: got {processed[0].inter_intra_tx_element!r}"
print(f"PASS: Inter/Intra = {processed[0].inter_intra_tx_element!r}")

print("\nAll integration tests passed!")
