"""Run internal tender scrapers as black-box tools.

These wrappers are used by the NCT extraction pipeline when enabled.
They do not modify scraper logic; they only execute the vendored scripts.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


# shared/llm.py → shared → project root  (parents[3] from nct_extraction/tender_tools.py)
# app/nct_extraction/tender_tools.py  →  parents[2] = project root
REPO_ROOT = Path(__file__).resolve().parents[2]

PFCCL_SCRIPT = Path(__file__).parent / "scrapers" / "pfcclindia_tender_scraper.py"
RECPDCL_SCRIPT = Path(__file__).parent / "scrapers" / "recpdcl_tender_scraper.py"


def _newest_subdir(dir_path: Path) -> Path | None:
    if not dir_path.exists():
        return None
    best: tuple[float, Path] | None = None
    for p in dir_path.iterdir():
        if not p.is_dir():
            continue
        try:
            ts = p.stat().st_mtime
        except Exception:
            continue
        if best is None or ts > best[0]:
            best = (ts, p)
    return best[1] if best else None


def _run_scraper(script_path: Path, *, query: str, output_dir: Path) -> Path:
    if not script_path.exists():
        raise FileNotFoundError(f"Scraper not found: {script_path}")

    output_dir.mkdir(parents=True, exist_ok=True)
    before = {p.name for p in output_dir.iterdir() if p.is_dir()}

    cmd = [
        sys.executable,
        str(script_path),
        "--query",
        query,
        "--output",
        str(output_dir),
    ]
    subprocess.run(cmd, cwd=str(REPO_ROOT), check=True)

    after = {p.name for p in output_dir.iterdir() if p.is_dir()}
    created = sorted(after - before)
    if len(created) == 1:
        return output_dir / created[0]
    return _newest_subdir(output_dir) or output_dir


def download_pfccl_tender_pdfs(*, query: str) -> Path:
    """Downloads PFCCL tender PDFs for the given exact-substring query."""
    uploads = REPO_ROOT / "uploads" / "PFCCL-INDIA-TENDER"
    return _run_scraper(PFCCL_SCRIPT, query=query, output_dir=uploads)


def download_recpdcl_tender_pdfs(*, query: str) -> Path:
    """Downloads RECPDCL/RECTPCL tender PDFs for the given exact-substring query."""
    uploads = REPO_ROOT / "uploads" / "RECPDCL-RECTPCL-TENDER"
    return _run_scraper(RECPDCL_SCRIPT, query=query, output_dir=uploads)
