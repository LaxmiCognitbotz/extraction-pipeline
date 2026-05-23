"""Shared Pydantic AI model helpers — fully self-contained.

Supports two model backends, selected via the ``LLM_PROVIDER`` env var:

  - **vm**     → Azure OpenAI via ``llm_client.bat`` (custom subprocess transport)
  - **google** → Google Gemini via ``pydantic-ai[google]``

All configuration is read directly from environment variables (or a ``.env``
file at the project root). This module has **no dependency on any other
sub-package** in this project.

Environment variables
---------------------
LLM_PROVIDER          : "vm" | "google"   (default: "google")
GOOGLE_API_KEY        : API key for Google Gemini
GOOGLE_MODEL          : model id           (default: "gemini-2.0-flash")
VM_SCRIPT_PATH        : absolute path to llm_client.bat (optional override)
VM_MODEL              : deployment name override for VM mode (optional)
"""

from __future__ import annotations

import json
import os
import asyncio
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import httpx
from pydantic_ai.models import Model

# ── Project root & .env loading ───────────────────────────────────────────────
# shared/llm.py lives at <project_root>/shared/llm.py  →  parent.parent = project root
_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv() -> None:
    """Minimal .env loader — only runs if python-dotenv is not installed."""
    env_file = _PROJECT_ROOT / ".env"
    if not env_file.exists():
        return
    try:
        from dotenv import load_dotenv
        load_dotenv(env_file, override=False)
    except ImportError:
        # Fallback: parse manually
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(key, value)


_load_dotenv()


# ── Config helpers (read from env directly) ───────────────────────────────────

def _provider() -> str:
    return os.environ.get("LLM_PROVIDER", "google").lower()

def _google_api_key() -> str:
    return os.environ.get("GOOGLE_API_KEY", "")

def _google_model() -> str:
    return os.environ.get("GOOGLE_MODEL", "gemini-2.0-flash")

def _vm_script_path() -> str:
    return os.environ.get("VM_SCRIPT_PATH", "")

def _vm_model() -> str:
    return os.environ.get("VM_MODEL", "")


# ── Public API ────────────────────────────────────────────────────────────────

def get_model() -> Model | str:
    """Return a Pydantic AI model instance for the configured provider.

    Returns:
        A model string (``google-gla:<model>``) for Google, or a fully
        configured ``OpenAIModel`` instance for VM/Azure.
    """
    provider = _provider()
    if provider == "vm":
        return _get_vm_model()
    return _get_google_model()


def ensure_api_key() -> None:
    """Ensure the appropriate API key is present in the environment.

    - Google: sets ``GOOGLE_API_KEY`` from env if not already set.
    - VM:     credentials are parsed from ``llm_client.bat``; no env var needed.
    """
    provider = _provider()
    if provider == "google":
        key = _google_api_key()
        if key and "GOOGLE_API_KEY" not in os.environ:
            os.environ["GOOGLE_API_KEY"] = key
    # VM mode: credentials come from the .bat file


# ── Google Gemini ─────────────────────────────────────────────────────────────

def _get_google_model() -> str:
    """Return the Pydantic AI model string for Google Gemini."""
    key = _google_api_key()
    if key:
        os.environ.setdefault("GOOGLE_API_KEY", key)
    return f"google-gla:{_google_model()}"


# ── VM / Azure OpenAI ─────────────────────────────────────────────────────────

def _get_bat_path() -> Path:
    """Resolve the path to llm_client.bat."""
    script_path = _vm_script_path()
    if script_path:
        bat_path = Path(script_path)
    else:
        bat_path = _PROJECT_ROOT / "llm_client.bat"

    if not bat_path.exists():
        raise FileNotFoundError(
            f"VM mode enabled but llm_client.bat not found at {bat_path}. "
            f"Set VM_SCRIPT_PATH in .env to the correct path."
        )
    return bat_path


class VMBatchTransport(httpx.AsyncBaseTransport):
    """Custom httpx transport that routes requests through llm_client.bat.

    The batch script receives the JSON request payload via a temp file and
    returns a JSON response on stdout — matching the Azure OpenAI wire format.
    """

    def __init__(self, bat_path: Path) -> None:
        self.bat_path = bat_path

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        await request.aread()
        payload_bytes = request.content
        temp_file_path = ""

        try:
            with tempfile.NamedTemporaryFile("wb", suffix=".json", delete=False) as tmp:
                tmp.write(payload_bytes)
                temp_file_path = tmp.name

            command = ["cmd", "/c", str(self.bat_path), temp_file_path]
            proc = await asyncio.create_subprocess_exec(
                *command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=os.environ.copy(),
            )
            stdout, stderr = await proc.communicate()

            if proc.returncode != 0:
                err_msg = stderr.decode("utf-8", errors="ignore").strip()
                raise RuntimeError(f"VM script failed (exit {proc.returncode}): {err_msg}")

            raw_output = stdout.decode("utf-8", errors="ignore").strip()
            if not raw_output:
                raise RuntimeError("LLM VM script returned empty output.")

            try:
                parsed = json.loads(raw_output)
                if "error" in parsed:
                    err_str = json.dumps(parsed["error"], indent=2)
                    print(f"\n[VM SCRIPT ERROR]\n{err_str}\n")
                    raise RuntimeError(f"Azure OpenAI API Error: {err_str}")
            except json.JSONDecodeError:
                print(f"\n[VM SCRIPT NON-JSON]\n{raw_output[:1000]}\n")
                raise RuntimeError(
                    f"Unexpected non-JSON response from VM script:\n{raw_output[:1000]}"
                )

            return httpx.Response(
                status_code=200,
                content=raw_output.encode("utf-8"),
                request=request,
            )
        finally:
            if temp_file_path and os.path.exists(temp_file_path):
                try:
                    os.remove(temp_file_path)
                except OSError:
                    pass


def _parse_bat_credentials(bat_path: Path) -> dict[str, str]:
    """Parse Azure OpenAI credentials from llm_client.bat.

    Expects lines of the form::

        set ENDPOINT=https://...
        set DEPLOYMENT=...
        set API_VERSION=...
        set KEY=...

    Returns:
        Dict with keys: ``endpoint``, ``deployment``, ``api_version``, ``api_key``.
    """
    creds: dict[str, str] = {}
    for line in bat_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line.lower().startswith("set "):
            continue
        assignment = line[4:].strip()
        if "=" not in assignment:
            continue
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

    missing = [k for k in ("endpoint", "deployment", "api_version", "api_key") if k not in creds]
    if missing:
        raise ValueError(
            f"Could not parse required credentials from {bat_path}: missing {missing}"
        )
    return creds


def _get_vm_model() -> Any:
    """Build an Azure OpenAI model routed through llm_client.bat."""
    from openai import AsyncAzureOpenAI
    from pydantic_ai.models.openai import OpenAIModel
    from pydantic_ai.providers.openai import OpenAIProvider

    bat_path = _get_bat_path()
    creds = _parse_bat_credentials(bat_path)
    deployment_name = _vm_model() or creds["deployment"]

    client = AsyncAzureOpenAI(
        api_key=creds["api_key"],
        api_version=creds["api_version"],
        azure_endpoint=creds["endpoint"],
        http_client=httpx.AsyncClient(transport=VMBatchTransport(bat_path)),
    )

    provider = OpenAIProvider(openai_client=client)
    return OpenAIModel(model_name=deployment_name, provider=provider)