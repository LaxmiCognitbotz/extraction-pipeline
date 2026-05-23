import sys
import re
import json
import time
from pathlib import Path
from typing import List, Optional
from pydantic import BaseModel, Field

import pdfplumber

# The project root is automatically in the path when running as a package

from pydantic_ai import Agent
from pydantic_ai.settings import ModelSettings
from shared.llm import get_model, ensure_api_key

# ── Pydantic Schemas for Scope Extraction ──

class ScopeElement(BaseModel):
    transmission_scope: str = Field(description="The granular scope item exactly as written, e.g., '3x1500MVA, 765/400kV GIS substation at Narela' or 'LILO of 765kV line'.")
    mva: Optional[str] = Field(description="Calculated MVA from the scope string. E.g., '3x1500MVA' -> '4500'. If multiple transformers like '6x1500 MVA + 5x500 MVA', add them -> '11500'. If no MVA, leave blank.")
    length: Optional[str] = Field(description="Length of the transmission line in km, if mentioned. If not, leave blank.")
    remarks: Optional[str] = Field(description="Any specific remarks, comments, or notes attached to this scope item in the table. Leave blank if none.")

class SchemeDetails(BaseModel):
    transmission_scheme: str = Field(description="The full name of the transmission scheme.")
    tender_issuing_authority: Optional[str] = Field(description="Bid Process Coordinator (BPC) assigned. Typically 'RECPDCL', 'PFCCL', 'CTUIL', or 'POWERGRID'.")
    execution_timeline: Optional[str] = Field(description="Implementation timeframe or completion date. If a duration is written, extract exactly as written e.g. '24 months', '36 months from date of allocation'. If a target date is written, extract as-is e.g. '31-03-2029', '31.03.2028'.")
    project_cost_cr: Optional[str] = Field(description="Estimated Project Cost in Crores (Cr), if mentioned. Just the number/string.")
    scope_elements: List[ScopeElement] = Field(description="The individual scope items/elements belonging to this scheme.")

# ── LLM Prompt ──

_SCOPE_SYSTEM_PROMPT = """
You are an expert data extractor for Indian CEA NCT (National Committee on Transmission) PDF documents.

Your task is to extract detailed information about a SPECIFIC Transmission Scheme from the provided PDF text/tables.
The user will provide the text of the PDF pages where the scheme is discussed.

You must extract the overall scheme details (BPC, Timeline, Cost) and then break down the 'Scope of Work' into individual `scope_elements` (like substations, lines, reactors, bays).

CRITICAL RULES FOR SCOPE ELEMENTS:
1. `transmission_scope`: Write out the full scope item description.
2. `mva`: You MUST mathematically calculate the total MVA if transformers are mentioned!
   - '3x1500MVA' = '4500'
   - '6x1500 MVA + 5x500 MVA' = 6*1500 + 5*500 = 9000 + 2500 = '11500'
   - '2x500MVA' = '1000'
   - If it's a transmission line or reactor with no MVA, return null or empty string.
3. `length`: Extract line length in km if specified.

CRITICAL RULES FOR SCHEME DETAILS:
1. `tender_issuing_authority`: Look for 'Bid Process Coordinator', 'BPC', or 'Implementing Agency'. Usually PFCCL or RECPDCL for TBCB.
2. `execution_timeline`: Look for 'Implementation timeframe' or 'Schedule'. (e.g., '24 months').
3. `project_cost_cr`: Look for 'Estimated Cost'.
"""

def _get_scope_agent():
    ensure_api_key()
    return Agent(
        model=get_model(),
        output_type=SchemeDetails,
        system_prompt=_SCOPE_SYSTEM_PROMPT,
        retries=2,
        model_settings=ModelSettings(temperature=0.0, max_tokens=4096, timeout=120)
    )

# ── Helper Functions ──

def _fuzzify_string(s: str) -> str:
    """Simplify string for robust searching (remove punctuation, lower case, normalize spaces)."""
    s = re.sub(r'[^a-zA-Z0-9\s]', '', s)
    s = re.sub(r'\s+', ' ', s)
    return s.strip().lower()

def _find_scheme_pages(pdf_path: str, scheme_name: str) -> list[int]:
    """Find which pages in the PDF discuss this specific scheme."""
    pages_found = set()
    target = _fuzzify_string(scheme_name)
    # We'll just look for a decent substring match since exact matches fail on PDF line breaks
    target_words = target.split()
    if len(target_words) > 5:
        # Use a sliding window of 5 words to find matches
        search_chunks = [" ".join(target_words[i:i+5]) for i in range(len(target_words)-4)]
    else:
        search_chunks = [target]

    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages):
            txt = page.extract_text()
            if not txt:
                continue
            txt_fuzzy = _fuzzify_string(txt)
            for chunk in search_chunks:
                if chunk in txt_fuzzy:
                    pages_found.add(i + 1)
                    # Include the next page too, since tables often span pages
                    if i + 1 < len(pdf.pages):
                        pages_found.add(i + 2)
                    break
    
    return sorted(list(pages_found))

def _extract_text_and_tables(pdf_path: str, pages: list[int]) -> str:
    parts = []
    
    with pdfplumber.open(pdf_path) as pdf:
        for pg_num in pages:
            page = pdf.pages[pg_num - 1]
            txt = page.extract_text() or ""
            parts.append(f"--- PAGE {pg_num} [RAW TEXT] ---")
            parts.append(txt)
    
    # Also try to dump camelot tables for structural clarity
    try:
        import camelot
        import logging
        logging.getLogger("camelot").setLevel(logging.ERROR)
        page_str = ",".join(str(p) for p in pages)
        tables = camelot.read_pdf(pdf_path, flavor="lattice", pages=page_str, strip_text="\n")
        if len(tables) == 0:
            tables = camelot.read_pdf(pdf_path, flavor="stream", pages=page_str, strip_text="\n")
            
        for t in tables:
            if not t.df.empty:
                parts.append(f"--- PAGE {t.page} [TABLE DATA] ---")
                parts.append(t.df.to_csv(index=False))
    except Exception as e:
        print(f"Camelot error (ignoring): {e}")
        pass
        
    return "\n\n".join(parts)

import difflib
import os
import glob
from app.nct_extraction.tender_query import suggest_queries
from app.nct_extraction.tbcb_extractor import extract_remarks_from_tbcb_report

try:
    from app.nct_extraction.scrapers import recpdcl_tender_scraper
    from app.nct_extraction.scrapers import pfcclindia_tender_scraper
except ImportError:
    recpdcl_tender_scraper = None
    pfcclindia_tender_scraper = None

def _fetch_external_tender_data(scheme_name: str, bpc: str) -> dict:
    """
    Executes the scraper tool calling logic to find and parse RFP/Amendment PDFs.
    """
    result = {
        "Date of tender issuance": "",
        "Date of Bid Submission": "",
        "Tentative SCOD": "",
        "Awarded To": ""
    }
    
    if not recpdcl_tender_scraper or not pfcclindia_tender_scraper:
        print("[!] Scraper modules not found. Skipping external tender data.")
        return result

    # 1. Determine which scraper to use
    # Non-TBCB authorities — skip scraping entirely (they have no public tender portal)
    NON_TBCB_BPC = {"POWERGRID", "PGCIL", "CTUIL", "CTU", "TOBEDECIDEDBYMOP", ""}
    bpc_upper = bpc.upper().replace(" ", "")
    if bpc_upper in NON_TBCB_BPC or "TOBEDECIDED" in bpc_upper:
        print(f"[-] BPC '{bpc}' is non-TBCB or undecided. Skipping scraping.")
        return result
    elif "RECPDCL" in bpc_upper or "RECTPCL" in bpc_upper:
        scraper = recpdcl_tender_scraper
    elif "PFCCL" in bpc_upper or "PFC" in bpc_upper or "PFFCL" in bpc_upper:
        scraper = pfcclindia_tender_scraper
    else:
        print(f"[-] Unrecognized BPC '{bpc}'. Skipping scraping.")
        return result

    # 2. Generate Queries: Search full name first, then fallback to shortened (max 5 retries)
    generated = suggest_queries(scheme_name, scope="") or []
    raw_queries = [scheme_name] + generated
    
    queries = []
    for q in raw_queries:
        if q not in queries:
            queries.append(q)
            
    queries = queries[:5]

    out_dir = Path("uploads/TEMP_TENDERS")
    out_dir.mkdir(parents=True, exist_ok=True)
    
    best_folder = None
    
    # 3. Search and download — bail fast if site is unreachable
    _site_unreachable = False
    for query in queries:
        if _site_unreachable:
            break
        print(f"[*] Running scraper for query: '{query}'")
        try:
            # The run() function downloads to out_dir / make_folder_name(query)
            scraper.run(query, out_dir)
            folder_name = scraper.make_folder_name(query)
            target_folder = out_dir / folder_name
            
            # If the folder exists and has PDFs, we found a match!
            if target_folder.exists() and list(target_folder.glob("*.pdf")):
                print(f"[+] Match found for query '{query}'. Breaking loop.")
                best_folder = target_folder
                break
        except Exception as e:
            err_str = str(e)
            print(f"[!] Scraper failed for query '{query}': {err_str[:120]}")
            # If site is completely unreachable, stop all retries immediately
            if "ERR_CONNECTION_RESET" in err_str or "ERR_CONNECTION_TIMED_OUT" in err_str or "net::ERR_" in err_str:
                print(f"[!] Site appears unreachable. Aborting remaining queries for this scheme.")
                _site_unreachable = True
            
    if not best_folder:
        return result
        
    # 4. Parse the downloaded PDFs
    pdf_files = list(best_folder.glob("*.pdf"))
    print(f"[*] Parsing {len(pdf_files)} PDFs in {best_folder}...")
    
    for pdf_path in pdf_files:
        filename = pdf_path.name.lower()
        try:
            with pdfplumber.open(pdf_path) as pdf:
                # Read just the first page for dates to save time
                first_page_text = pdf.pages[0].extract_text().lower() if pdf.pages else ""
                
                # Logic for Date of tender issuance (RFP Document)
                if "rfp" in filename or "request for proposal" in first_page_text:
                    # Look for a date near the bottom right (rough heuristic)
                    date_match = re.search(r'\d{1,2}\s+[a-zA-Z]+\s+\d{4}', first_page_text)
                    if date_match and not result["Date of tender issuance"]:
                        result["Date of tender issuance"] = date_match.group(0)
                        
                # Logic for Bid Submission & Tentative SCOD (Amendment)
                if "amend" in filename or "corrigendum" in filename:
                    # In a real implementation, you'd parse the 'event' table here
                    date_match = re.search(r'bid submission.*?(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})', first_page_text)
                    if date_match:
                        result["Date of Bid Submission"] = date_match.group(1)
                        
                # Logic for Awarded To (Results/Successful)
                if "result" in filename or "successful" in filename or "awarded" in filename:
                    # Extract rank 1 bidder
                    if "rank 1" in first_page_text or "l1" in first_page_text:
                        result["Awarded To"] = "[Parsed Bidder Name]"
        except Exception as e:
            print(f"[!] Failed to parse {pdf_path.name}: {e}")

    return result

def extract_nct_scope_for_scheme(pdf_path: str, scheme_name: str, meeting_label: str) -> List[dict]:
    """Pass 1: Extract granular scope details directly from the NCT PDF."""
    pages = _find_scheme_pages(pdf_path, scheme_name)
    if not pages:
        print(f"    [!] Could not find pages for scheme: {scheme_name[:50]}...")
        return []
        
    print(f"    [>] Found scheme on pages: {pages}")
    context = _extract_text_and_tables(pdf_path, pages)
    
    agent = _get_scope_agent()
    prompt = (
        f"Extract details for the following Transmission Scheme:\n"
        f"NAME: {scheme_name}\n\n"
        f"PDF CONTEXT:\n{context}\n"
    )
    
    try:
        result = agent.run_sync(prompt)
        details: SchemeDetails = result.output
    except Exception as e:
        print(f"    [!] LLM Extraction failed: {e}")
        return []
        
    initial_rows = []
    
    if details.scope_elements:
        for scope in details.scope_elements:
            row = {
                "Transmission Scheme": details.transmission_scheme,
                "Transmission Scope": scope.transmission_scope,
                "MVA": scope.mva or "",
                "Status": "Approved",
                "Approval of Elements in which NCT": re.search(r'(\d+)', meeting_label).group(1) if re.search(r'(\d+)', meeting_label) else meeting_label,
                "Source": "NCT",
                "Tender Issuing Authority": details.tender_issuing_authority or "",
                "Date of tender issuance": "",
                "Date of Bid Submission": "",
                "Execution Timeline": details.execution_timeline or "",
                "Tentative SCOD": "",
                "Awarded To": "",
                "Project Cost (Cr.)": details.project_cost_cr or "",
                "SPV Transfer Date": "",
                "original SCOD": "",
                "Antipicated SCOD": "",
                "Remarks": scope.remarks or ""
            }
            initial_rows.append(row)
    else:
        # Fallback if no granular scope items found
        row = {
            "Transmission Scheme": details.transmission_scheme or scheme_name,
            "Transmission Scope": "",  
            "MVA": "",
            "Status": "Approved",
            "Approval of Elements in which NCT": re.search(r'(\d+)', meeting_label).group(1) if re.search(r'(\d+)', meeting_label) else meeting_label,
            "Source": "NCT",
            "Tender Issuing Authority": details.tender_issuing_authority or "",
            "Date of tender issuance": "",
            "Date of Bid Submission": "",
            "Execution Timeline": details.execution_timeline or "",
            "Tentative SCOD": "",
            "Awarded To": "",
            "Project Cost (Cr.)": details.project_cost_cr or "",
            "SPV Transfer Date": "",
            "original SCOD": "",
            "Antipicated SCOD": "",
            "Remarks": ""
        }
        initial_rows.append(row)
        
    return initial_rows

def _find_pdf_for_meeting(meeting_label: str, pdf_dir: Path) -> Optional[Path]:
    """Given a label like '39th NCT Meeting', find the corresponding PDF like '02_39th_NCT_MoM.pdf'."""
    m = re.search(r'(\d+)', meeting_label)
    if not m:
        return None
    meeting_num = m.group(1)
    
    for pdf_path in pdf_dir.glob("*.pdf"):
        if f"{meeting_num}th" in pdf_path.name.lower() or f"_{meeting_num}_" in pdf_path.name.lower():
            return pdf_path
            
    return None

def main():
    sys.stdout.reconfigure(encoding='utf-8')
    
    registry_path = Path("scheme_seed_registry.json")
    if not registry_path.exists():
        registry_path = Path("nct_extraction/output/scheme_seed_registry.json")
        
    if not registry_path.exists():
        print(f"Registry file not found at {registry_path.absolute()}")
        return
        
    with open(registry_path, "r", encoding="utf-8") as f:
        registry = json.load(f)
        
    pdf_dir = Path("uploads/CEA-NCT-Minutes")
    intermediate_rows = []
    
    print("\n" + "="*80)
    print("🚀 PHASE 1: PURE NCT DATA EXTRACTION (PAGE BY PAGE)")
    print("="*80)
    
    # ── PHASE 1: Extract all NCT Data First ──
    for meeting_label, schemes in registry.items():
        if not schemes: continue
            
        correct_pdf_path = _find_pdf_for_meeting(meeting_label, pdf_dir)
        if not correct_pdf_path or not correct_pdf_path.exists():
            continue
            
        print(f"\n📄 Scanning PDF: {correct_pdf_path.name} for {meeting_label}")
        
        for scheme in schemes:
            print(f"\n--- Extracting NCT Scope for: {scheme[:80]}... ---")
            try:
                rows = extract_nct_scope_for_scheme(str(correct_pdf_path), scheme, meeting_label)
                if rows:
                    intermediate_rows.extend(rows)
                    print(f"    ✓ Extracted {len(rows)} scope elements.")
                else:
                    print(f"    - No scope elements found.")
            except Exception as e:
                print(f"    ! Error extracting scheme: {e}")
            time.sleep(1)
                
    # Save intermediate results in case Phase 2 crashes
    intermediate_file = Path("output/intermediate_nct_scopes.json")
    intermediate_file.parent.mkdir(exist_ok=True)
    with open(intermediate_file, "w", encoding="utf-8") as f:
        json.dump(intermediate_rows, f, indent=4, ensure_ascii=False)
        
    print("\n" + "="*80)
    print("🌐 PHASE 2: EXTERNAL SCRAPING & TBCB ENRICHMENT")
    print("="*80)
    
    # ── PHASE 2: Fetch external fields based on the extracted BPC ──
    # We group by scheme name to avoid scraping the same scheme multiple times for each granular scope element
    processed_schemes = {}
    
    final_rows = []
    for row in intermediate_rows:
        scheme_name = row["Transmission Scheme"]
        bpc = row["Tender Issuing Authority"]
        
        if scheme_name not in processed_schemes:
            print(f"\n--- Fetching External Data for: {scheme_name[:80]}... ---")
            tbcb_remarks = extract_remarks_from_tbcb_report(scheme_name)
            tender_data = _fetch_external_tender_data(scheme_name, bpc)
            processed_schemes[scheme_name] = {
                "tbcb_remarks": tbcb_remarks,
                "tender_data": tender_data
            }
        
        ext_data = processed_schemes[scheme_name]
        
        # Merge the external data into the row
        row["Remarks"] = row["Remarks"] if row["Remarks"] else (ext_data["tbcb_remarks"] or "")
        row["Date of tender issuance"] = ext_data["tender_data"]["Date of tender issuance"]
        row["Date of Bid Submission"] = ext_data["tender_data"]["Date of Bid Submission"]
        row["Tentative SCOD"] = ext_data["tender_data"]["Tentative SCOD"]
        row["Awarded To"] = ext_data["tender_data"]["Awarded To"]
        
        final_rows.append(row)
        
    out_file = Path("output/final_extracted_scopes.json")
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(final_rows, f, indent=4, ensure_ascii=False)
        
    print(f"\n✅ Done! Extracted {len(final_rows)} total scope elements across all PDFs.")
    print(f"💾 Saved final enriched output to {out_file.absolute()}")

if __name__ == "__main__":
    main()

