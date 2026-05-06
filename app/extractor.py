"""Markdown -> Structured JSON extractor using Pydantic AI.

Uses a Pydantic AI Agent with ``output_type=list[TransmissionElement]``
for native structured output.  Supports three LLM backends:

  - **groq**   → Groq Cloud (openai/gpt-oss-120b)
  - **vm**     → Azure OpenAI via llm_client.bat
  - **google** → Google Gemini

Sends the **entire document in one call** — modern large-context models
handle even the biggest CEA/CTUIL reports (~100K chars) comfortably.

No chunking needed. No manual JSON parsing. No truncation salvaging.
"""

from __future__ import annotations

import time
from pathlib import Path

from pydantic_ai import Agent
from pydantic_ai.settings import ModelSettings

from app.business_logic import post_process_elements
from app.config import settings
from app.llm import ensure_api_key, get_model
from app.schemas import (
    DocType,
    ExtractionResult,
    TransmissionElement,
)


# ── System Prompt ──────────────────────────────────────────────────────


def _load_system_prompt() -> str:
    """Load the extraction system prompt from the prompts directory."""
    prompt_path = settings.prompts_dir / "system_prompt.md"
    if not prompt_path.exists():
        raise FileNotFoundError(
            f"System prompt not found at {prompt_path}. "
            "Ensure prompts/system_prompt.md exists."
        )
    return prompt_path.read_text(encoding="utf-8")


# ── Agent Factory ──────────────────────────────────────────────────────


def _get_agent() -> Agent[None, list[TransmissionElement]]:
    """Create and return the extraction Agent.

    Key Pydantic AI features used:
    - ``output_type=list[TransmissionElement]``: Native structured output.
      The model returns validated Pydantic objects directly.
    - ``retries``: Auto-retry on validation errors or API failures.
    - ``model_settings``: Temperature=0 for deterministic extraction,
      high max_tokens to handle large output arrays.

    The model backend is selected by ``LLM_PROVIDER`` env var.
    """
    ensure_api_key()
    system_prompt = _load_system_prompt()
    model = get_model()

    # Adjust max_tokens based on provider capabilities
    max_tokens = 16384
    timeout = 300  # 5 minutes default
    if settings.llm_provider == "google":
        max_tokens = 65536
    elif settings.llm_provider == "groq":
        # Groq gpt-oss-120b supports up to 16K output tokens
        max_tokens = 16384
        timeout = 180
    elif settings.llm_provider == "vm":
        # Azure OpenAI — conservative
        max_tokens = 16384
        timeout = 300

    agent: Agent[None, list[TransmissionElement]] = Agent(
        model=model,
        output_type=list[TransmissionElement],
        system_prompt=system_prompt,
        retries=settings.agent_retries,
        model_settings=ModelSettings(
            temperature=settings.temperature,
            max_tokens=max_tokens,
            timeout=timeout,
        ),
    )

    return agent


# ── User Message Builder ──────────────────────────────────────────────


def _build_user_message(
    markdown_content: str,
    doc_type: DocType,
    region: str = "",
) -> str:
    """Build the user message with DOC_TYPE header and full markdown body."""
    header = f"DOC_TYPE: {doc_type.value}"
    if region:
        header += f"\nREGION: {region}"
    return f"{header}\n\n{markdown_content}"


# ── Main Extraction Entry Point ────────────────────────────────────────


def extract_elements(
    markdown_path: str | Path,
    doc_type: str | DocType,
    region: str = "",
) -> ExtractionResult:
    """Extract transmission elements from a Markdown file in a single call.

    Sends the entire document to the Pydantic AI Agent. The backend
    model (Groq / Azure VM / Gemini) handles the structured extraction.

    Args:
        markdown_path: Path to the converted Markdown file.
        doc_type: One of ``RTM_UC_Report``, ``TBCB_Comm_Report``,
                  ``TBCB_UC_Report``, ``NCT_Report``, ``General``.
        region: Optional region name for contextual disambiguation.

    Returns:
        An ``ExtractionResult`` with validated ``TransmissionElement``
        objects.
    """
    md_path = Path(markdown_path).resolve()
    if not md_path.exists():
        raise FileNotFoundError(f"Markdown file not found: {md_path}")

    if isinstance(doc_type, str):
        doc_type = DocType(doc_type)

    md_content = md_path.read_text(encoding="utf-8")
    print(f"[extractor] Read {md_path.name}  ({len(md_content):,} chars)")

    # Build agent and send entire document in one call
    agent = _get_agent()
    user_message = _build_user_message(md_content, doc_type, region)

    print(
        f"[extractor] Sending document to {settings.model_name} "
        f"(provider: {settings.llm_provider}) via Pydantic AI ..."
    )

    start_time = time.time()

    # Retry on transport-level errors (server disconnect, read timeout)
    # Pydantic AI retries only cover validation/API errors, not httpx transport
    max_transport_retries = 3
    last_error = None

    for attempt in range(1, max_transport_retries + 1):
        try:
            result = agent.run_sync(user_message)
            elements: list[TransmissionElement] = result.data

            elapsed = time.time() - start_time
            print(
                f"[extractor] [OK] {len(elements)} elements extracted "
                f"in {elapsed:.1f}s"
            )
            _print_usage(result)

            # Apply deterministic business logic (codes, status, MVA, etc.)
            elements = post_process_elements(elements, doc_type)

            return ExtractionResult(
                doc_type=doc_type,
                region=region,
                source_pdf="",
                source_markdown=str(md_path),
                element_count=len(elements),
                elements=elements,
            )

        except Exception as e:
            error_name = type(e).__name__
            # Retry on transport / timeout errors
            is_transport = any(
                keyword in error_name.lower() or keyword in str(e).lower()
                for keyword in (
                    "remoteprotocol", "readtimeout", "disconnect",
                    "connection", "timeout",
                )
            )
            if is_transport and attempt < max_transport_retries:
                wait = 2 ** attempt * 5  # 10s, 20s, 40s
                print(
                    f"[extractor] [RETRY] {error_name} on attempt {attempt}. "
                    f"Waiting {wait}s before retry ..."
                )
                time.sleep(wait)
                last_error = e
                continue
            else:
                raise

    # Should not reach here, but just in case
    raise last_error  # type: ignore


# ── Utilities ──────────────────────────────────────────────────────────


def _print_usage(result) -> None:
    """Print token usage stats from the agent run result, if available."""
    try:
        usage = result.usage()
        if usage:
            parts = []
            if usage.request_tokens:
                parts.append(f"input={usage.request_tokens:,}")
            if usage.response_tokens:
                parts.append(f"output={usage.response_tokens:,}")
            if usage.total_tokens:
                parts.append(f"total={usage.total_tokens:,}")
            if parts:
                print(f"[extractor] Tokens: {', '.join(parts)}")
    except Exception:
        pass  # Usage info not available -- that's fine
