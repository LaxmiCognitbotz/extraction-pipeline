"""Quick test for generate_inter_intra naming convention."""
from app.business_logic import generate_inter_intra

tests = [
    # (scheme_name, expected_col_c)
    (
        "Transmission system strengthening scheme for evacuation of power from solar energy zones in Rajasthan (8.1 GW) under phase- II- Part G",
        "RJ Ph-II Part G",
    ),
    (
        "Transmission system associated with LTA applications from Rajasthan SEZ Phase-III Part-C1",
        "RJ Ph-III Part C1 SEZ",
    ),
    (
        "Transmission system for evacuation of power from REZ in Rajasthan (20 GW) under Phase-III part H",
        "RJ Ph-III Part H",
    ),
    (
        "Transmission Scheme for Evacuation of Power from Potential Renewable Energy Zone in Khavda area of Gujarat under Phase-IV (7 GW): Part E2",
        "GJ & Khavda Ph-IV Part E2",
    ),
    (
        "Transmission system for evacuation of power from Rajasthan REZ Ph-IV (Part-1) (Bikaner Complex): PART-A",
        "RJ & Bikaner Ph-IV Part 1",
    ),
    (
        "Transmission Scheme for Solar Energy Zone in Bidar (2500 MW), Karnataka",
        "Bidar",
    ),
    (
        "Transmission System Strengthening for interconnections of Bhadla-III and Bikaner-III complex",
        "Str. Bhadla-III & Bikaner-III",
    ),
    (
        "Dynamic Reactive Compensation at KPS1 and KPS3",
        "KPS-I & KPS-III",
    ),
    (
        "Transmission system for evacuation of power from REZ in Rajasthan (20 GW) under Phase-III Part-B1",
        "RJ Ph-III Part B1",
    ),
    (
        "Transmission Scheme for Evacuation of power from REZ in Rajasthan (20 GW) under Phase-III Part D",
        "RJ Ph-III Part D",
    ),
    (
        "Transmission System for evacuation of additional 7 GW RE Power from Khavda RE Park under Phase-III Part B",
        "Khavda Ph-III Part B",
    ),
    (
        "Transmission System for Evacuation of Power from Rajasthan REZ PH-IV (PART-3: 6GW) [BIKANER COMPLEX]",
        "RJ & Bikaner Ph-IV Part 3",  # code normalizes to Capitalize()
    ),
    (
        "System Strengthening at Koppal-II and Gadag-II for integration of RE generation projects",
        "Str. Koppal-II & Gadag-II",
    ),
    (
        "Transmission System for Evacuation of Power from Rajasthan REZ Ph-IV (Part-2: 5.5 GW) (Jaisalmer/Barmer Complex)",
        "RJ & Jaisalmer/Barmer Ph-IV Part 2",
    ),
    (
        "Transmission scheme for Solar Energy Zone in Ananthpuram (Ananthapur) (2500 MW) and Kurnool (1000 MW), Andhra Pradesh",
        "Ananthpuram & Ananthapur & Kurnool",  # location-based, no phase
    ),
    (
        "Transmission system associated with LTA applications from Rajasthan SEZ Part-E",
        "RJ Part E SEZ",  # Part E but no Phase explicitly stated
    ),
]

passed = 0
failed = 0
for scheme, expected in tests:
    result = generate_inter_intra(scheme)
    status = "PASS" if result == expected else "FAIL"
    if status == "FAIL":
        failed += 1
        print(f"  {status}: got={result!r}  expected={expected!r}")
        print(f"         scheme={scheme[:80]}")
    else:
        passed += 1
        print(f"  {status}: {result}")

print(f"\n{passed} passed, {failed} failed out of {len(tests)}")
