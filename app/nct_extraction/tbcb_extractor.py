import re
import sys
from pathlib import Path
from typing import Optional
import pdfplumber

from pydantic_ai import Agent
from pydantic_ai.settings import ModelSettings
from pydantic import BaseModel, Field
from shared.llm import get_model, ensure_api_key

class TbcbRemarks(BaseModel):
    remarks: str = Field(description="The remarks text found for the scheme. If not found, leave blank.")

def _get_tbcb_agent():
    ensure_api_key()
    return Agent(
        model=get_model(),
        output_type=TbcbRemarks,
        system_prompt="You are an expert at extracting data from CEA TBCB Under Construction reports. Given the text/tables of a page and a Target Scheme Name, extract the exact 'Remarks' or 'Current Status' text associated with that scheme. Do not hallucinate.",
        retries=2,
        model_settings=ModelSettings(temperature=0.0, max_tokens=1024, timeout=60)
    )

def extract_remarks_from_tbcb_report(scheme_name: str, report_pdf_path: str = "uploads/CTUIL-Transmission-Reports/2026/03_March/TBCB_UC_Report.pdf") -> Optional[str]:
    """
    Search for a transmission scheme in the TBCB Under Construction report
    and extract the 'Remarks' column using an LLM (with a heuristic fallback).
    """
    if not Path(report_pdf_path).exists():
        print(f"[!] TBCB report not found: {report_pdf_path}")
        return None
        
    words = [w for w in re.split(r'\W+', scheme_name) if len(w) > 3]
    if len(words) >= 4:
        search_query = " ".join(words[:4]).lower()
    else:
        search_query = scheme_name.lower()
        
    print(f"Searching TBCB Report for: '{search_query}'")

    matched_page_text = ""
    heuristic_remarks = None
    
    with pdfplumber.open(report_pdf_path) as pdf:
        for i, page in enumerate(pdf.pages):
            text = page.extract_text()
            if not text:
                continue
                
            if search_query in text.lower():
                matched_page_text = text
                # Generate a heuristic fallback
                tables = page.extract_tables()
                for table in tables:
                    for row in table:
                        row_text = " ".join([str(cell) for cell in row if cell]).lower()
                        if search_query in row_text:
                            r = str(row[-1]) if row[-1] else (str(row[-2]) if len(row) > 1 else "")
                            if r.strip():
                                heuristic_remarks = r.replace('\n', ' ').strip()
                                break
                if not heuristic_remarks:
                    lines = text.split('\n')
                    for j, line in enumerate(lines):
                        if search_query in line.lower():
                            heuristic_remarks = " ".join(lines[j:j+3])
                            break
                break

    if not matched_page_text:
        return None
        
    # Attempt LLM Extraction first for better intelligence
    try:
        agent = _get_tbcb_agent()
        prompt = f"TARGET SCHEME: {scheme_name}\n\nPAGE CONTEXT:\n{matched_page_text}\n\nExtract the Remarks column text."
        result = agent.run_sync(prompt)
        llm_remarks = result.output.remarks.strip()
        if llm_remarks:
            print("[tbcb-extractor] LLM extraction successful.")
            return llm_remarks
    except Exception as e:
        print(f"[tbcb-extractor] LLM extraction failed (e.g. 403 error): {e}. Falling back to heuristic.")
        
    return heuristic_remarks

if __name__ == "__main__":
    test_scheme = "Kudankulam Unit - 3 & 4"
    print("Testing TBCB Remarks Extraction...")
    remarks = extract_remarks_from_tbcb_report(test_scheme)
    print(f"Remarks found: {remarks}")
