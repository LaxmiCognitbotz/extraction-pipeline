# CTUIL Renewable Energy Margin Extraction Pipeline Usage

This pipeline extracts structured data from CTUIL's Renewable Energy Margin PDFs (Non-RE, Proposed RE, and RE Substations) and outputs three JSON files and a combined 3-sheet styled Excel file.

## 1. Extract All New PDFs (Default)
**Command:**
```powershell
uv run python -m app.re_margin_extraction.run_re_margin_extraction
```
**Example:**
```powershell
uv run python -m app.re_margin_extraction.run_re_margin_extraction
```
**What it does:**
Scans all subfolders in `uploads/CTUIL-Renewable-Energy/Margin/`. It checks the existing JSON files in `outputs/RE-Margin/` and **automatically skips** any PDFs that have already been extracted. It extracts only new PDFs and safely appends their data to the existing sheets.

## 2. Extract Only a Specific Folder
**Command:**
```powershell
uv run python -m app.re_margin_extraction.run_re_margin_extraction --folder "<folder_name>"
```
**Example:**
```powershell
uv run python -m app.re_margin_extraction.run_re_margin_extraction --folder "non-re"
```
**What it does:**
Runs the extraction *only* for the `Non-RE` folder. Valid options are `--folder "non-re"`, `--folder "proposed-re"`, or `--folder "re-substations"`. It skips the other folders entirely (but loads their old data so the final Excel file still contains all 3 sheets).

## 3. Extract a Specific File
**Command:**
```powershell
uv run python -m app.re_margin_extraction.run_re_margin_extraction --file "<filename.pdf>"
```
**Example:**
```powershell
uv run python -m app.re_margin_extraction.run_re_margin_extraction --file "01_Non RE SS Margin 31 08 2025.pdf"
```
*(Optional) You can also explicitly specify the folder to skip auto-detection:*
**Command:**
```powershell
uv run python -m app.re_margin_extraction.run_re_margin_extraction --folder "<folder_name>" --file "<filename.pdf>"
```
**Example:**
```powershell
uv run python -m app.re_margin_extraction.run_re_margin_extraction --folder "non-re" --file "01_Non RE SS Margin 31 08 2025.pdf"
```
**What it does:**
Extracts only the specified PDF. You don't even need to specify the folder—the pipeline will search the subfolders and auto-detect where it belongs (unless you provide `--folder` explicitly). It automatically removes any old rows for this specific file from the master JSON and appends the newly extracted rows. Data for all other PDFs remains intact.

## 4. Force Re-Extract Everything
**Command:**
```powershell
uv run python -m app.re_margin_extraction.run_re_margin_extraction --force
```
**Example:**
```powershell
uv run python -m app.re_margin_extraction.run_re_margin_extraction --force
```
**What it does:**
Ignores the skip logic and re-extracts **every single PDF** across all 3 categories from scratch. Useful if you want to completely rebuild the database.

## 5. Test on a Limited Number of PDFs
**Command:**
```powershell
uv run python -m app.re_margin_extraction.run_re_margin_extraction --limit <number>
```
**Example:**
```powershell
uv run python -m app.re_margin_extraction.run_re_margin_extraction --limit 2
```
**What it does:**
Limits processing to the top 2 most-recent PDFs per folder for quick testing. Still skips already-extracted PDFs unless combined with `--force`.
