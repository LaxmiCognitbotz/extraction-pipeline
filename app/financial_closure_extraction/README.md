# CTUIL Financial Closure Extraction Pipeline Usage

This pipeline extracts structured data from CTUIL's Compliance PDFs (such as Financial Closure and Land Documents deadlines). It outputs a flat JSON file and a styled Excel sheet with all rows from all tables across the documents.

## 1. Extract All New PDFs (Default)
**Command:**
```powershell
uv run python -m app.financial_closure_extraction.run_ctuil_extraction
```
**Example:**
```powershell
uv run python -m app.financial_closure_extraction.run_ctuil_extraction
```
**What it does:**
Scans the `uploads/CTUIL-Compliance-PDFs` folder for all PDFs. It checks `outputs/CTUIL-Compliance/ctuil_compliance_raw.json` and **automatically skips** any PDFs that have already been extracted. It extracts only new PDFs and safely appends their data to your master JSON and Excel files.

## 2. Force Re-Extract Everything
**Command:**
```powershell
uv run python -m app.financial_closure_extraction.run_ctuil_extraction --force
```
**Example:**
```powershell
uv run python -m app.financial_closure_extraction.run_ctuil_extraction --force
```
**What it does:**
Ignores the skip logic and re-extracts **every single PDF** in the folder from scratch. Useful if you want to completely rebuild the database.

## 3. Extract or Update a Single PDF
**Command:**
```powershell
uv run python -m app.financial_closure_extraction.run_ctuil_extraction --file "<filename.pdf>"
```
**Example:**
```powershell
uv run python -m app.financial_closure_extraction.run_ctuil_extraction --file "01_List of land and FC complianc.pdf"
```
**What it does:**
Extracts only the specified PDF. This acts as an implicit `--force` for that specific file. It deletes any old rows for this specific PDF in the master JSON and appends the newly extracted rows. Data for all other PDFs is preserved.

## 4. Test on a Limited Number of PDFs
**Command:**
```powershell
uv run python -m app.financial_closure_extraction.run_ctuil_extraction --limit <number>
```
**Example:**
```powershell
uv run python -m app.financial_closure_extraction.run_ctuil_extraction --limit 1
```
**What it does:**
Limits processing to the single most recent PDF for quick testing. Still respects the skip logic unless combined with `--force`.
