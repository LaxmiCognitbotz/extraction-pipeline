"""Utilities to decide what query to feed tender scrapers.

PFCCL/RECPDCL tender scrapers do an exact *substring* match on the tender title.
So the best "query" is a short, unique phrase that is likely to appear in the
title (not the entire NCT scheme/scope sentence).
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


_STOP = {
    "transmission",
    "system",
    "scheme",
    "for",
    "of",
    "the",
    "and",
    "under",
    "phase",
    "part",
    "package",
    "including",
    "associated",
    "works",
    "substation",
    "line",
    "lines",
    "bay",
    "bays",
    "kv",
    "mva",
    "icts",
    "ict",
    "grid",
    "power",
    "evacuation",
    "augmentation",
}


def suggest_queries(scheme: str, scope: str, limit: int = 8) -> list[str]:
    text = f"{scheme} {scope}".strip()
    if not text:
        return []

    candidates: list[str] = []

    # 1) Voltage phrases (often present in titles)
    volts = re.findall(r"\b\d{3}\s*kV\b|\b\d{3}kV\b", text, flags=re.IGNORECASE)
    volts = [v.replace(" ", "").upper() for v in volts]
    volts = list(dict.fromkeys(volts))
    if volts:
        candidates.append(" ".join(volts[:2]))

    # 2) Location-ish phrases after "at", "near", "from", "to"
    for kw in [" at ", " near ", " from ", " to "]:
        parts = text.split(kw)
        if len(parts) >= 2:
            frag = parts[1]
            frag = re.split(r"[.;,\n()]", frag)[0].strip()
            frag = re.sub(r"\s+", " ", frag)
            if 4 <= len(frag) <= 80:
                candidates.append(frag)

    # 3) REZ / SEZ / common abbreviations
    for abbr in ["REZ", "SEZ", "ISTS", "TBCB", "RTM"]:
        if re.search(rf"\b{abbr}\b", text, flags=re.IGNORECASE):
            candidates.append(abbr)

    # 4) Token-based phrases: keep meaningful words and create short substrings
    toks = [t.lower() for t in re.split(r"[^A-Za-z0-9]+", text) if len(t) >= 4]
    toks = [t for t in toks if t not in _STOP and not t.isdigit()]
    uniq: list[str] = []
    seen: set[str] = set()
    for t in toks:
        if t not in seen:
            seen.add(t)
            uniq.append(t)

    # Build 2- and 3-token phrases
    for n in [2, 3]:
        for i in range(0, len(uniq) - n + 1):
            phrase = " ".join(uniq[i : i + n])
            if 8 <= len(phrase) <= 60:
                candidates.append(phrase)

    # De-dupe, prefer earlier
    out: list[str] = []
    seen2: set[str] = set()
    for c in candidates:
        c = c.strip()
        if not c:
            continue
        key = c.lower()
        if key in seen2:
            continue
        seen2.add(key)
        out.append(c)
        if len(out) >= limit:
            break
    return out


def _cli() -> int:
    if len(sys.argv) != 2:
        print("Usage: python -m nct_extraction.tender_query <nct_report.json>")
        return 2

    path = Path(sys.argv[1])
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = data.get("rows", [])
    for i, r in enumerate(rows, 1):
        scheme = r.get("transmission_scheme", "")
        scope = r.get("transmission_scope", "")
        sugg = suggest_queries(scheme, scope)
        if not sugg:
            continue
        print(f"\n[{i}] {scheme[:80]}")
        print(f"    scope: {scope[:120]}")
        for s in sugg:
            print(f"    - {s}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())

