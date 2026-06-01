# CTUIL Revocation 24.6 Extraction Pipeline Usage

This pipeline extracts structured data from CTUIL's Regulation 24.6 Revocation PDFs and outputs both a master JSON and a styled Excel file.

## 1. Extract All New PDFs (Default)
**Command:**
```powershell
uv run python -m app.revocation_extraction.run_revocation_extraction
```
**What it does:**
Scans the `uploads/CTUIL-Revocations-PDFs` folder for all PDFs. It safely checks the existing `outputs/Revocations-24.6/revocations_extracted.json` file and **automatically skips** any PDFs that have already been extracted. It only runs AI extraction on *new* PDFs and appends their rows to your master JSON and Excel files.

## 2. Force Re-Extract Everything
**Command:**
```powershell
uv run python -m app.revocation_extraction.run_revocation_extraction --force
```
**What it does:**
Ignores the skip logic and re-extracts **every single PDF** in the folder from scratch. Useful if you want to completely rebuild the database (e.g. after updating the AI prompt or logic).

## 3. Extract or Update a Single PDF
**Command:**
```powershell
uv run python -m app.revocation_extraction.run_revocation_extraction --file "01_Final list Jul'26.pdf"
```
**What it does:**
Extracts only the specified PDF. This acts as an implicit `--force` for that specific file. It will remove any old rows for this specific PDF in the master JSON and append the freshly extracted rows. It does **not** delete data belonging to other PDFs.

## 4. Test on a Limited Number of Recent PDFs
**Command:**
```powershell
uv run python -m app.revocation_extraction.run_revocation_extraction --limit 2
```
**What it does:**
Runs the pipeline but stops after processing the top 2 PDFs. Very useful for quick testing. Still respects the skip logic unless you combine it with `--force`.
