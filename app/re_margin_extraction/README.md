# CTUIL Renewable Energy Margin Extraction Pipeline Usage

This pipeline extracts structured data from CTUIL's Renewable Energy Margin PDFs (Non-RE, Proposed RE, and RE Substations) and outputs three JSON files and a combined 3-sheet styled Excel file.

## 1. Extract All New PDFs (Default)
**Command:**
```powershell
uv run python -m app.re_margin_extraction.run_re_margin_extraction
```
**What it does:**
Scans all subfolders in `uploads/CTUIL-Renewable-Energy/Margin/`. It checks the existing JSON files in `outputs/RE-Margin/` and **automatically skips** any PDFs that have already been extracted. It extracts only new PDFs and safely appends their data to the existing sheets.

## 2. Force Re-Extract Everything
**Command:**
```powershell
uv run python -m app.re_margin_extraction.run_re_margin_extraction --force
```
**What it does:**
Ignores the skip logic and re-extracts **every single PDF** across all 3 categories from scratch. Useful if you want to completely rebuild the database.

## 3. Extract or Update a Single PDF
**Command:**
```powershell
uv run python -m app.re_margin_extraction.run_re_margin_extraction --file "01_Non RE SS Margin 31 08 2025.pdf"
```
**What it does:**
Extracts only the specified PDF (it will auto-detect which category folder it belongs to). This automatically forces re-extraction for this specific file, removing its old rows from the master JSON and appending the newly extracted rows. Data for all other PDFs remains intact.

## 4. Extract Only a Specific Category
**Command:**
```powershell
uv run python -m app.re_margin_extraction.run_re_margin_extraction --folder "non-re"
```
**What it does:**
Runs the extraction *only* for the `Non-RE` folder. You can use `--folder "non-re"`, `--folder "proposed-re"`, or `--folder "re-substations"`.

## 5. Test on a Limited Number of PDFs
**Command:**
```powershell
uv run python -m app.re_margin_extraction.run_re_margin_extraction --limit 2
```
**What it does:**
Limits processing to the top 2 PDFs per folder for quick testing. Still skips already-extracted PDFs unless combined with `--force`.
