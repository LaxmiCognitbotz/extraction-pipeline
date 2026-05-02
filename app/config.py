"""Application configuration using Pydantic BaseSettings.

Loads environment variables from ``.env`` and provides typed access
to all pipeline settings including API keys, model configuration,
and filesystem paths.
"""

from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Pipeline configuration loaded from environment variables and .env file."""

    # ==== LLM Configuration ====
    google_api_key: str
    model_name: str = "gemini-3-flash-preview"
    temperature: float = 0.0
    
    # ==== Retry Configuration ====
    agent_retries: int = 3

    # ==== Paths ====
    project_root: Path = Path(__file__).resolve().parent.parent
    uploads_dir: Path = project_root / "uploads"
    output_dir: Path = project_root / "output"
    prompts_dir: Path = project_root / "prompts"
    
    # ==== opendataloader-pdf ====
    pdf_output_format: str = "markdown"

    model_config = {
        "env_file": str(Path(__file__).resolve().parent.parent / ".env"),
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }


# Singleton instance
settings = Settings()
