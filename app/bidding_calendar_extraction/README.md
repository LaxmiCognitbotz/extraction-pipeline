# CTUIL Bidding Calendar Extraction Pipeline Usage

This pipeline extracts structured data from CTUIL's Bidding Calendar PDFs and outputs both a flat JSON array and a beautifully styled Excel sheet (expanded to one row per major element).

## 1. Extract All New PDFs (Default)
**Command:**
```powershell
uv run python -m app.bidding_calendar_extraction.run_bidding_calendar_extraction
```
**Example:**
```powershell
uv run python -m app.bidding_calendar_extraction.run_bidding_calendar_extraction
```
**What it does:**
Scans the `uploads/CTUIL-Bidding-Calendar` folder for all PDFs. It safely checks the existing `outputs/Bidding-Calendar/bidding_calendar_extracted.json` file and **automatically skips** any PDFs that have already been extracted. It extracts only new PDFs and safely appends their data to your master files.

## 2. Force Re-Extract Everything
**Command:**
```powershell
uv run python -m app.bidding_calendar_extraction.run_bidding_calendar_extraction --force
```
**Example:**
```powershell
uv run python -m app.bidding_calendar_extraction.run_bidding_calendar_extraction --force
```
**What it does:**
Ignores the skip logic and re-extracts **every single PDF** in the folder from scratch. Useful if you want to completely rebuild the database.

## 3. Extract or Update a Single PDF
**Command:**
```powershell
uv run python -m app.bidding_calendar_extraction.run_bidding_calendar_extraction --file "<filename.pdf>"
```
**Example:**
```powershell
uv run python -m app.bidding_calendar_extraction.run_bidding_calendar_extraction --file "01_Bidding Calendar 31-03-2026.pdf"
```
**What it does:**
Extracts only the specified PDF. This acts as an implicit `--force` for that specific file. It deletes any old rows for this specific PDF in the master JSON and appends the newly extracted rows. Data for all other PDFs is preserved.

## 4. Test on a Limited Number of PDFs
**Command:**
```powershell
uv run python -m app.bidding_calendar_extraction.run_bidding_calendar_extraction --limit <number>
```
**Example:**
```powershell
uv run python -m app.bidding_calendar_extraction.run_bidding_calendar_extraction --limit 1
```
**What it does:**
Limits processing to the single most recent PDF for quick testing. Still respects the skip logic unless combined with `--force`.
