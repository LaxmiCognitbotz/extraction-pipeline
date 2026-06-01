# LangExtract Revocation Pipeline Usage

This is an alternative extraction pipeline for CTUIL Regulation 24.6 Revocation PDFs powered directly by Google's `langextract` library. It processes documents page-by-page and outputs a flat JSON file.

## 1. Extract All New PDFs (Default)
**Command:**
```powershell
uv run python -m app.langextract_revocation_extraction.run_pipeline
```
**What it does:**
Scans the `uploads/CTUIL-Revocations-PDFs` folder for all PDFs. It checks `outputs/LangExtract-Revocations/revocations_langextract.json` and **automatically skips** any PDFs that have already been extracted. It extracts new PDFs and appends their rows to the JSON.

## 2. Force Re-Extract Everything
**Command:**
```powershell
uv run python -m app.langextract_revocation_extraction.run_pipeline --force
```
**What it does:**
Ignores the skip logic and forces a fresh extraction for **every single PDF** in the folder from scratch.

## 3. Extract or Update a Single PDF
**Command:**
```powershell
uv run python -m app.langextract_revocation_extraction.run_pipeline --file "01_Final list Jul'26.pdf"
```
**What it does:**
Extracts only the specified PDF. This acts as an implicit `--force` for that specific file. It deletes any old rows for this PDF from the master JSON and appends the newly extracted rows, keeping data from other PDFs totally safe.

## 4. Test on a Limited Number of PDFs
**Command:**
```powershell
uv run python -m app.langextract_revocation_extraction.run_pipeline --limit 2
```
**What it does:**
Runs the pipeline but stops after processing 2 PDFs. Useful for quickly testing the `langextract` model config. Still skips already-extracted PDFs unless `--force` is provided.
