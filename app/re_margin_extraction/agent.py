"""
Pydantic-AI agent module for CTUIL Renewable Energy Margin PDF extraction.

System prompts are built dynamically from each model's ``schema_info()``
classmethod so column names, nested paths and carry-forward rules are kept
in ONE place (models.py) and flow automatically into the prompts.

Page/table extraction is delegated to shared.pdf_table_extractor, which
provides annotated-markdown tables with column-path legends, multi-row
header detection, span carry-forward, and duplicate suppression.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import pdfplumber
from pydantic_ai import Agent

from shared.llm import get_model, ensure_api_key
from shared.pdf_table_extractor import build_page_bundle, truncate_bundle, DEFAULT_MAX_BUNDLE_CHARS
from app.re_margin_extraction.models import (
    NonRESubstationMarginResult,
    ProposedRESubstationMarginResult,
    RESubstationMarginResult,
    get_schema_info,
)

logger = logging.getLogger(__name__)

# =─────────────────────────────────────────────────────────────────────────────
# Dynamic system-prompt builder  (derives everything from schema_info)
# =─────────────────────────────────────────────────────────────────────────────

def _build_system_prompt(kind: str) -> str:
    """
    Build the full system prompt for a given PDF kind by introspecting the
    corresponding Pydantic model's schema_info().  No column names are
    hardcoded here — every detail is derived from the model.

    The generated prompt has four sections:
      1. Role + report-type header (from schema_info pdf_title / row_noun)
      2. Core extraction rules (universal + carry-forward rules from schema)
      3. CRITICAL nested-column-mapping block (built from nested_fields)
      4. Anti-hallucination rules
    """
    info = get_schema_info(kind)

    pdf_title  = info["pdf_title"]
    row_noun   = info["row_noun"]
    cf_fields  = info["carry_forward"]   # [(alias, description), …]
    nested     = info["nested_fields"]   # [(parent, [sub, …]), …] or
                                         # [(parent, [(mid, [leaf, …]), …]), …]

    # ── 1. Role header ──────────────────────────────────────────────────
    role = (
        f"You are a data extraction agent for {pdf_title} PDF reports.\n"
        f"Extract every {row_noun} row by reading BOTH the \""
        "=== PAGE TEXT ===\" and the \"=== TABLE ===\" sections."
    )

    # ── 2. Core rules ─────────────────────────────────────────────────
    # Build carry-forward rules dynamically from schema
    cf_rules: list[str] = []
    for i, (alias, desc) in enumerate(cf_fields, start=2):
        cf_rules.append(f"{i}. {desc}")

    num_start = len(cf_fields) + 2   # next rule number after carry-forward rules

    fixed_rules = [
        "1. \"=== PAGE TEXT ===\" is your primary source of truth. "
        "Use it to recover values when TABLE cells are empty or null.",
    ] + cf_rules + [
        f"{num_start}. Never skip any {row_noun} rows.",
        f"{num_start + 1}. If a cell contains '0' (zero), extract it as '0'. "
        "Do NOT convert '0' to null. Only blank cells, dashes '-', or spaces should be null.",
        f"{num_start + 2}. If a row is clearly a 'Total' or 'Sub-total' row (e.g., 'Total GUJ:', 'Total MAH:', 'Sub-total'), completely SKIP and IGNORE it. Do not extract it as a {row_noun}.",
        f"{num_start + 3}. For the 'Name of station' field, extract ONLY the actual substation name. Strip off any trailing voltage levels (e.g., convert 'Navinal (GIS) 765/400kV' to 'Navinal (GIS)', and 'Aurangabad 765/400/220kV' to 'Aurangabad').",
        f"{num_start + 4}. For 'Existing / UC/ Planned MVA Capacity', if there are multiple lines/newlines, replace the newline with a comma (e.g., '6x1500MVA, 765/400kV \\n1x500MVA' -> '6x1500MVA, 765/400kV, 1x500MVA').",
        f"{num_start + 5}. Extract all other field values exactly as printed in the source.",
    ]

    if kind == "re-substations":
        fixed_rules.append(
            f"{num_start + 6}. Summary/Complex row skipping: If a row represents a parent 'Complex' (e.g., its name is exactly 'Bhadla Complex', 'Fatehgarh-Barmer Complex', 'Bikaner Complex', or similar, without any other substation names) AND it has specific individual sub-stations/sub-rows listed directly underneath it (usually indexed with letters like 'a', 'b', 'c' under the Complex's main Sl. No.), you MUST skip and IGNORE the main parent Complex summary row itself. Only extract the individual detailed sub-stations underneath it (e.g., extract 'Bhadla' and 'Bhadla-II', but skip 'Bhadla Complex').\n"
            "CRITICAL: Do NOT skip any row if its name contains a specific substation designation after the complex name. For example, '(Fatehgarh-Barmer Complex) Barmer-I', '(Fatehgarh-Barmer Complex) Barmer-II', '(Fatehgarh-Barmer Complex) Fatehgarh-IV (Section-II)', '(Bikaner Complex) Bikaner-IV', or '(Bikaner Complex) Bikaner-VI' are NOT parent complex rows and MUST be extracted."
        )
        fixed_rules.append(
            f"{num_start + 7}. Clean the Category field: Strip any leading letter index from it (e.g., convert 'A. Existing RE Pooling Stations' to 'Existing RE Pooling Stations', and 'B. Commissioning between Jul-25 to Dec-25' to 'Commissioning between Jul-25 to Dec-25')."
        )

    rules_block = "Rules:\n" + "\n".join(fixed_rules)

    # ── 3. Nested-column-mapping block ───────────────────────────────────
    nested_lines: list[str] = [
        "CRITICAL — Nested Column Mapping (read carefully):",
        "The TABLE headers in the Markdown grid may be split across multiple rows due to merged cells.",
        "You MUST carefully map the sub-columns to their parent columns based on their visual alignment in the Markdown.",
        "",
        "IMPORTANT RULES FOR MATCHING COLUMNS:",
        "- Ignore minor spelling mistakes or newlines in the table headers (e.g., 'Aditional Margin' matches 'Additional Margin').",
        "- If two columns have the exact same label but you expect one to be for ICT Augmentation, map them based on their logical left-to-right ordering in the table.",
        "",
        "Expected nested column groups for this report type:",
    ]

    for parent, subs in nested:
        # subs is either [str, …] (one-level) or [(str, [str,…]), …] (two-level)
        if subs and isinstance(subs[0], tuple):
            # Two-level nesting (e.g. Transformation Capacity > Existing > 765/400kV)
            for mid, leaves in subs:
                if leaves:
                    for leaf in leaves:
                        nested_lines.append(f"  {parent} > {mid} > {leaf}")
                else:
                    nested_lines.append(f"  {parent} > {mid}")
        else:
            # One-level nesting
            for sub in subs:
                nested_lines.append(f"  {parent} > {sub}")

    nested_lines += [
        "",
        "Example: a column path of \"X > Y\" means the cell value belongs at:",
        "  { \"X\": { \"Y\": <value> } }    in the output JSON.",
    ]
    nested_block = "\n".join(nested_lines)

    # ── 4. Anti-hallucination rules ────────────────────────────────────
    anti_halluc = (
        "Anti-Hallucination Rules:\n"
        "- ONLY extract values explicitly present in the text. Never invent or estimate.\n"
        "- If a value is genuinely absent, return null. Never fill in a number not printed.\n"
        f"- Each {row_noun} row is independent — never copy values between rows.\n"
        "- Never extrapolate or infer numeric values."
    )

    return "\n\n".join([role, rules_block, nested_block, anti_halluc])


# Warm-up cache: build prompts once at import time and store them.
# This avoids rebuilding on every agent creation call.
_PROMPT_CACHE: dict[str, str] = {}


def _get_system_prompt(kind: str) -> str:
    if kind not in _PROMPT_CACHE:
        _PROMPT_CACHE[kind] = _build_system_prompt(kind)
        logger.debug("[%s] system prompt built (%d chars)", kind, len(_PROMPT_CACHE[kind]))
    return _PROMPT_CACHE[kind]



# =─────────────────────────────────────────────────────────────────────────────
# Filename date helper
# =─────────────────────────────────────────────────────────────────────────────

_FILENAME_DATE_RE = re.compile(r"(\d{2})[-_ ]?(\d{2})[-_ ]?(\d{4})")


def _date_from_filename(filename: str) -> str | None:
    """Extract DD-MM-YYYY from filename like '01_SS Margin 31 08 2025.pdf'."""
    m = _FILENAME_DATE_RE.search(filename)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return None





# =─────────────────────────────────────────────────────────────────────────────
# Agents (lazy singletons)
# =─────────────────────────────────────────────────────────────────────────────

_non_re_agent: Agent | None = None
_proposed_re_agent: Agent | None = None
_re_agent: Agent | None = None


def _get_agent(kind: str) -> Agent:
    global _non_re_agent, _proposed_re_agent, _re_agent
    ensure_api_key()
    model = get_model()
    system_prompt = _get_system_prompt(kind)

    if kind == "non-re":
        if _non_re_agent is None:
            _non_re_agent = Agent(
                model=model,
                output_type=NonRESubstationMarginResult,
                system_prompt=system_prompt,
            )
            logger.info("Non-RE Agent ready.")
        return _non_re_agent
    elif kind == "proposed-re":
        if _proposed_re_agent is None:
            _proposed_re_agent = Agent(
                model=model,
                output_type=ProposedRESubstationMarginResult,
                system_prompt=system_prompt,
            )
            logger.info("Proposed RE Agent ready.")
        return _proposed_re_agent
    elif kind == "re-substations":
        if _re_agent is None:
            _re_agent = Agent(
                model=model,
                output_type=RESubstationMarginResult,
                system_prompt=system_prompt,
            )
            logger.info("RE Substations Agent ready.")
        return _re_agent
    else:
        raise ValueError(f"Unknown margin PDF kind: {kind}")


# =─────────────────────────────────────────────────────────────────────────────
# 3-tier loop-detection fallback
# =─────────────────────────────────────────────────────────────────────────────

import time

def run_agent_with_retry(agent: Agent, prompt: str, max_retries: int = 5) -> Any:
    for attempt in range(1, max_retries + 1):
        try:
            return agent.run_sync(prompt)
        except Exception as exc:
            err_str = str(exc).lower()
            is_rate_limit = (
                "429" in err_str 
                or "resource_exhausted" in err_str 
                or "quota exceeded" in err_str 
                or "rate limit" in err_str 
                or "limit: 0" in err_str
            )
            if is_rate_limit and attempt < max_retries:
                # Parse retry delay if present, otherwise default to 60s
                wait_sec = 60
                import re
                match = re.search(r"retry in ([\d\.]+)s", err_str)
                if match:
                    try:
                        wait_sec = int(float(match.group(1))) + 2
                    except ValueError:
                        pass
                logger.warning(
                    "Rate limit/Quota hit. Attempt %d/%d. Sleeping for %d seconds...",
                    attempt, max_retries, wait_sec
                )
                time.sleep(wait_sec)
                continue
            # Raise other exceptions or if we exhausted retries
            raise exc


def _run_single_page_with_fallback(
    agent: Agent,
    page_md: str,
    page_num: int,
    total: int,
    kind: str,
    filename: str,
    all_records: list,
    detected_date_ref: list,
) -> None:
    """
    Extract one page with 3-tier loop-detection escalation:
    Tier 1 — full bundle (DEFAULT_MAX_BUNDLE_CHARS).
    Tier 2 — half-sized truncation.
    Tier 3 — half-sized + [ignoring loop detection] prefix.
    """
    bundle = f"=== PAGE {page_num} of {total} ===\n{page_md}"
    prompt_prefix = "Extract all margin records:\n\n"
    tiers = [
        (DEFAULT_MAX_BUNDLE_CHARS,      False),
        (DEFAULT_MAX_BUNDLE_CHARS // 2, False),
        (DEFAULT_MAX_BUNDLE_CHARS // 2, True),
    ]

    for tier_num, (char_limit, ignore_loop) in enumerate(tiers, start=1):
        current = truncate_bundle(bundle, max_chars=char_limit)
        user_msg = (
            ("[ignoring loop detection] " if ignore_loop else "")
            + prompt_prefix + current
        )
        try:
            sub_result = run_agent_with_retry(agent, user_msg).output
            if sub_result.as_on_date and not detected_date_ref[0]:
                detected_date_ref[0] = sub_result.as_on_date
            all_records.extend(sub_result.records)
            if tier_num > 1:
                logger.info(
                    "[%s] [%s] page %d recovered on tier-%d",
                    kind.upper(), filename, page_num, tier_num,
                )
            return
        except Exception as e:
            if tier_num < len(tiers):
                logger.warning(
                    "[%s] [%s] page %d tier-%d failed (%s) — escalating",
                    kind.upper(), filename, page_num, tier_num, e,
                )
                continue
            logger.error(
                "[%s] [%s] page %d tier-%d failed: %s",
                kind.upper(), filename, page_num, tier_num, e, exc_info=True,
            )
            raise e


# =─────────────────────────────────────────────────────────────────────────────
# Extraction functions
# =─────────────────────────────────────────────────────────────────────────────

def detect_page_kind(page_text: str, default_kind: str) -> str:
    text_lower = page_text.lower()
    
    # 1. New RE Substations format (contains specific new columns)
    if (
        "re potential" in text_lower
        or "connectivity granted" in text_lower
        or "margin for connectivity" in text_lower
        or "effectiveness of gna" in text_lower
        or "connectivity under process" in text_lower
    ):
        return "re-substations"
        
    # 2. Old Proposed RE format
    if (
        "transformation capacity (mva)" in text_lower
        or "capacity allocated (mw)" in text_lower
        or "no. of trfs required" in text_lower
        or "additional margin on existing" in text_lower
        or "aditional margin on existing" in text_lower
        or "additional margin with ict" in text_lower
    ):
        return "proposed-re"
        
    # 3. Non-RE format
    if (
        "existing / uc/ planned mva capacity" in text_lower 
        or "remarks / total addl. margins" in text_lower
        or "line bays required" in text_lower
    ):
        return "non-re"
        
    return default_kind


def extract_margin_pdf(
    pdf_path: Path,
    kind: str,
    pages_per_chunk: int = 1,
) -> list[Any]:
    """
    Extract all margin records from a single margin PDF using opendataloader-pdf
    """
    filename = pdf_path.name
    filename_date = _date_from_filename(filename)
    detected_date_ref: list[str | None] = [None]
    all_records = []

    # Step 1: Generate Markdown using opendataloader-pdf
    md_out_dir = Path("outputs/RE-Margin/Markdown")
    md_out_dir.mkdir(parents=True, exist_ok=True)
    generated_md_path = md_out_dir / (pdf_path.stem + ".md")

    logger.info("[%s] [%s] Converting PDF to Markdown...", kind.upper(), filename)
    import opendataloader_pdf
    opendataloader_pdf.convert(
        str(pdf_path),
        format='markdown',
        markdown_page_separator='\n\n--- PAGE %page-number% ---\n\n',
        output_dir=str(md_out_dir),
        quiet=True
    )
    logger.info("[%s] [%s] Markdown saved to %s", kind.upper(), filename, generated_md_path)

    # Step 2: Read Markdown and split into pages
    with open(generated_md_path, "r", encoding="utf-8") as f:
        md_content = f.read()

    import re
    # Split by the separator we defined, allowing optional whitespace/newlines
    pages_raw = re.split(r'(?:\r?\n)*--- PAGE \d+ ---(?:\r?\n)*', md_content)
    # The first element might be empty or pre-page-1 junk, keep only non-empty
    pages = [p.strip() for p in pages_raw if p.strip()]
    total = len(pages)
    logger.info("[%s] [%s] %d page(s) extracted via Markdown", kind.upper(), filename, total)

    # Step 3: Group pages by detected kind dynamically
    grouped_pages: list[tuple[str, list[tuple[int, str]]]] = []
    for idx, page_md in enumerate(pages):
        page_num = idx + 1
        page_kind = detect_page_kind(page_md, kind)
        if not grouped_pages or grouped_pages[-1][0] != page_kind:
            grouped_pages.append((page_kind, []))
        grouped_pages[-1][1].append((page_num, page_md))

    # Step 4: Chunk pages within each group and feed to respective LLM agent
    for grp_kind, grp_pages in grouped_pages:
        grp_agent = _get_agent(grp_kind)
        logger.info(
            "[%s] [%s] Processing group of kind '%s' containing %d page(s)",
            kind.upper(), filename, grp_kind.upper(), len(grp_pages)
        )
        
        for start in range(0, len(grp_pages), pages_per_chunk):
            chunk = grp_pages[start: start + pages_per_chunk]
            parts = []
            chunk_page_nums = []
            for p_num, p_md in chunk:
                parts.append(f"=== PAGE {p_num} of {total} ===\n{p_md}")
                chunk_page_nums.append(p_num)
            
            raw_bundle = "\n\n".join(parts)
            bundle = truncate_bundle(raw_bundle)
            
            logger.info("[%s] [%s] chunk with pages %s → LLM (%s)", kind.upper(), filename, chunk_page_nums, grp_kind.upper())
            try:
                result = run_agent_with_retry(
                    grp_agent,
                    f"Extract all margin records:\n\n{bundle}"
                ).output

                if result.as_on_date and not detected_date_ref[0]:
                    detected_date_ref[0] = result.as_on_date
                all_records.extend(result.records)

            except Exception as exc:
                logger.warning(
                    "[%s] [%s] chunk with pages %s failed (%s) — retrying 1 page at a time with escalation fallback",
                    kind.upper(), filename, chunk_page_nums, exc,
                )
                for p_num, p_md in chunk:
                    _run_single_page_with_fallback(
                        grp_agent, p_md, p_num, total, grp_kind, filename,
                        all_records, detected_date_ref,
                    )

    # Finalize dates and inject metadata
    as_on = detected_date_ref[0] or filename_date
    for rec in all_records:
        rec.source_file = filename
        if not rec.as_on_date:
            rec.as_on_date = as_on

    # Programmatic carry-forward for fields defined in the record's schema_info()
    if all_records:
        first_rec = all_records[0]
        if hasattr(first_rec, "schema_info"):
            cf_keys = [k for k, _ in first_rec.schema_info().get("carry_forward", [])]
            if cf_keys:
                current_values = {k: None for k in cf_keys}
                for rec in all_records:
                    for key in cf_keys:
                        # Find the actual Pydantic attribute name for this alias key
                        attr_name = None
                        for fname, finfo in type(rec).model_fields.items():
                            alias = finfo.alias or fname
                            if alias == key:
                                attr_name = fname
                                break
                        if attr_name:
                            val = getattr(rec, attr_name, None)
                            if val and str(val).strip():
                                current_values[key] = val
                            else:
                                setattr(rec, attr_name, current_values[key])

    # Programmatic filtering of duplicate/summary parent complex rows
    if all_records:
        to_remove = set()
        pooling_stations = [
            getattr(r, "pooling_station", None)
            for r in all_records
            if getattr(r, "pooling_station", None)
        ]
        if pooling_stations:
            for idx, rec in enumerate(all_records):
                ps_name = getattr(rec, "pooling_station", None)
                if not ps_name:
                    continue
                name = ps_name.strip()
                if name.lower().endswith(" complex"):
                    base = name[:-8].strip()
                    base_lower = base.lower()
                    
                    has_children = False
                    for other_name in pooling_stations:
                        if other_name == ps_name:
                            continue
                        other_lower = other_name.lower()
                        if other_lower == base_lower or other_lower.startswith(base_lower + "-"):
                            has_children = True
                            break
                        if base_lower == "fatehgarh-barmer":
                            if "fatehgarh" in other_lower or "barmer" in other_lower:
                                has_children = True
                                break
                    if has_children:
                        logger.info("[%s] [%s] Filtering out parent complex summary row: '%s'", kind.upper(), filename, ps_name)
                        to_remove.add(idx)
            if to_remove:
                all_records = [rec for idx, rec in enumerate(all_records) if idx not in to_remove]

    logger.info("[%s] [%s] %d record(s) extracted", kind.upper(), filename, len(all_records))
    return all_records
