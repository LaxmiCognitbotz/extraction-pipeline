# NCT Minutes Extraction Pipeline

This folder contains a specialized extraction pipeline for CEA National Committee on Transmission (NCT) meeting minutes.

## Overview
The pipeline uses `pdfplumber` to extract text and tables from NCT PDF documents and `pydantic-ai` to convert that data into structured JSON.

## Extracted Fields
- **Element Code**: Sl.No or unique identifier for the element.
- **Scheme Name**: The full project/scheme name.
- **Scope**: Detailed description of the work.
- **Capacity (MVA)**: Calculated total capacity.
- **Execution Timeline**: Months or date for completion.
- **Project Cost (Cr.)**: Estimated cost in Crores.
- **Length (km)**: Transmission line length.

## Usage
1. Ensure `.env` is configured with a valid `GOOGLE_API_KEY` or `LLM_PROVIDER`.
2. Run the extraction:
   ```bash
   python nct_extraction/main.py
   ```
3. Results will be saved in `nct_extraction/output/nct_extraction_results.json`.

## Structure
- `schemas.py`: Pydantic models for data validation.
- `extractor.py`: Core logic for PDF parsing and AI agent interaction.
- `main.py`: Batch processing script.
