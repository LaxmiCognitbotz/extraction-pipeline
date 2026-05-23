"""Application configuration using Pydantic BaseSettings.

Loads environment variables from ``.env`` and provides typed access
to all pipeline settings including API keys, model configuration,
and filesystem paths.

Supports two LLM provider modes:
  - ``vm``     → Azure OpenAI via ``llm_client.bat``
  - ``google`` → Google Gemini
"""

from pathlib import Path
from typing import Literal, Optional

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Pipeline configuration loaded from environment variables and .env file."""

    # ==== LLM Provider Selection ====
    llm_provider: Literal["vm", "google"] = "google"

    # ==== Google Gemini ====
    google_api_key: str = ""
    google_model: str = "gemini-2.0-flash"

    # ==== VM / Azure OpenAI ====
    # VM mode calls llm_client.bat which has Azure creds baked in.
    # The bat script path can be overridden here.
    vm_script_path: Optional[str] = None
    vm_model: Optional[str] = None

    # ==== Shared LLM Settings ====
    temperature: float = 0.0

    # ==== Retry Configuration ====
    agent_retries: int = 3

    # ==== Paths ====
    project_root: Path = Path(__file__).resolve().parent.parent.parent
    uploads_dir: Path = project_root / "uploads"
    output_dir: Path = project_root / "output"
    prompts_dir: Path = project_root / "prompts"

    # ── Convenience Properties ──

    @property
    def model_name(self) -> str:
        """Return the active model identifier for logging."""
        if self.llm_provider == "vm":
            return self.vm_model if self.vm_model else "azure-openai-vm"
        return self.google_model

    model_config = {
        "env_file": str(Path(__file__).resolve().parent.parent.parent / ".env"),
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }


# Singleton instance
settings = Settings()
