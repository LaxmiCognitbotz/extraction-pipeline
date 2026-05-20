import sys
from pathlib import Path

# Setup paths
project_root = str(Path(__file__).parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from nct_extraction.scope_extractor import _fetch_external_tender_data
from nct_extraction.tbcb_extractor import extract_remarks_from_tbcb_report

def main():
    sys.stdout.reconfigure(encoding='utf-8')
    
    # We will test a known scheme from the 18th NCT Meeting that is currently 
    # under construction and has been tendered.
    test_scheme = "System strengthening at Koppal-II and Gadag-II for integration of RE generation projects"
    
    # Let's say the BPC was RECPDCL (often the case for TBCB).
    test_bpc = "RECPDCL"
    
    print("="*60)
    print(f"TESTING EXTERNAL DATA FETCH PIPELINE")
    print(f"Scheme: {test_scheme}")
    print(f"BPC: {test_bpc}")
    print("="*60)
    
    print("\n[1] Fetching Remarks from TBCB UC Report...")
    remarks = extract_remarks_from_tbcb_report(test_scheme)
    if remarks:
        print(f"  --> FOUND REMARKS: {remarks}")
    else:
        print("  --> NO REMARKS FOUND.")
        
    print("\n[2] Fetching Dates from Tender Site...")
    # This will run the scraper, download the PDFs, and parse them.
    tender_data = _fetch_external_tender_data(test_scheme, test_bpc)
    
    print("\n[3] TENDER DATA RESULTS:")
    import json
    print(json.dumps(tender_data, indent=4))
    
    print("\nTEST COMPLETE!")

if __name__ == "__main__":
    main()
