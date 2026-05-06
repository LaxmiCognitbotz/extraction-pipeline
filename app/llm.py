"""Pydantic AI model helpers — multi-provider support.

Supports three model backends configurable via ``LLM_PROVIDER`` env var:

  - **groq**   → Groq Cloud (``openai/gpt-oss-120b``) via ``pydantic-ai[groq]``
  - **vm**     → Azure OpenAI via ``llm_client.bat`` (wrapped as custom OpenAI provider)
  - **google** → Google Gemini via ``pydantic-ai[google]``

The VM mode uses the Azure OpenAI endpoint configured in ``llm_client.bat``,
wrapping it as an OpenAI-compatible provider so Pydantic AI's structured
output works seamlessly.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from pydantic_ai.models import Model

from app.config import settings


def get_model() -> Model | str:
    """Return a Pydantic AI model instance for the configured provider.

    Returns:
        Either a model string (for simple providers) or a configured
        Model instance (for VM/Azure).
    """
    provider = settings.llm_provider

    if provider == "groq":
        return _get_groq_model()
    elif provider == "vm":
        return _get_vm_model()
    else:  # google
        return _get_google_model()


def _get_groq_model() -> str:
    """Configure Groq Cloud model.

    Pydantic AI uses the ``groq:<model>`` string format.
    The GROQ_API_KEY env var is read automatically by the Groq provider.
    """
    if settings.groq_api_key:
        os.environ.setdefault("GROQ_API_KEY", settings.groq_api_key)

    model_id = settings.groq_model
    return f"groq:{model_id}"


def _get_google_model() -> str:
    """Configure Google Gemini model.

    Pydantic AI uses the ``google-gla:<model>`` string format.
    The GOOGLE_API_KEY env var is read automatically by the Google provider.
    """
    if settings.google_api_key:
        os.environ.setdefault("GOOGLE_API_KEY", settings.google_api_key)

    return f"google-gla:{settings.google_model}"


def _get_vm_model() -> Any:
    """Configure Azure OpenAI model via the VM batch script credentials.

    Reads Azure credentials from ``llm_client.bat`` and creates an
    OpenAI-compatible model pointing at the Azure endpoint.

    This lets Pydantic AI handle structured output natively instead of
    going through the raw batch script subprocess.
    """
    from openai import AsyncAzureOpenAI

    from pydantic_ai.models.openai import OpenAIModel
    from pydantic_ai.providers.openai import OpenAIProvider

    # Parse credentials from llm_client.bat
    creds = _parse_bat_credentials()

    client = AsyncAzureOpenAI(
        api_key=creds["api_key"],
        api_version=creds["api_version"],
        azure_endpoint=creds["endpoint"],
    )

    deployment_name = settings.vm_model if settings.vm_model else creds["deployment"]

    provider = OpenAIProvider(openai_client=client)
    model = OpenAIModel(
        model_name=deployment_name,
        provider=provider,
    )

    return model


def _parse_bat_credentials() -> dict[str, str]:
    """Parse Azure OpenAI credentials from llm_client.bat.

    The batch file has lines like:
        set ENDPOINT=https://...
        set DEPLOYMENT=...
        set API_VERSION=...
        set KEY=...

    Returns:
        Dict with keys: endpoint, deployment, api_version, api_key.
    """
    # Look for llm_client.bat relative to project root or workspace root
    bat_path = None
    if settings.vm_script_path:
        bat_path = Path(settings.vm_script_path)
    else:
        # Default: look in workspace root (one level above extraction-pipeline)
        workspace_root = settings.project_root.parent
        bat_path = workspace_root / "llm_client.bat"

    if not bat_path.exists():
        raise FileNotFoundError(
            f"VM mode enabled but llm_client.bat not found at {bat_path}. "
            f"Set VM_SCRIPT_PATH in .env to the correct path."
        )

    creds: dict[str, str] = {}
    bat_content = bat_path.read_text(encoding="utf-8")

    for line in bat_content.splitlines():
        line = line.strip()
        if line.lower().startswith("set "):
            # Parse: set KEY=VALUE
            assignment = line[4:].strip()
            if "=" in assignment:
                key, value = assignment.split("=", 1)
                key = key.strip().upper()
                value = value.strip()
                if key == "ENDPOINT":
                    creds["endpoint"] = value
                elif key == "DEPLOYMENT":
                    creds["deployment"] = value
                elif key == "API_VERSION":
                    creds["api_version"] = value
                elif key == "KEY":
                    creds["api_key"] = value

    required_keys = ["endpoint", "deployment", "api_version", "api_key"]
    missing = [k for k in required_keys if k not in creds]
    if missing:
        raise ValueError(
            f"Could not parse required credentials from {bat_path}: "
            f"missing {missing}"
        )

    return creds


def ensure_api_key() -> None:
    """Ensure the appropriate API key is available in the environment.

    Each provider reads its key from a specific env var:
    - Groq: GROQ_API_KEY
    - Google: GOOGLE_API_KEY
    - VM: Credentials are in llm_client.bat (no env var needed)
    """
    provider = settings.llm_provider

    if provider == "groq":
        if settings.groq_api_key and "GROQ_API_KEY" not in os.environ:
            os.environ["GROQ_API_KEY"] = settings.groq_api_key
    elif provider == "google":
        if settings.google_api_key and "GOOGLE_API_KEY" not in os.environ:
            os.environ["GOOGLE_API_KEY"] = settings.google_api_key
    # VM mode: credentials are parsed from bat file, no env var needed