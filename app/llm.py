"""Pydantic AI model helpers.

Provides utility functions for configuring the Google Gemini model
used by the extraction Agent. The actual Agent is created in
``extractor.py``; this module only provides shared helpers.
"""

import os

from app.config import settings


def get_model_id() -> str:
    """Return the full Pydantic AI model identifier for Google Gemini.

    Pydantic AI uses the format ``google-gla:<model-name>`` for
    Google Generative Language API models.

    Returns:
        Model identifier string, e.g. ``google-gla:gemini-2.0-flash``.
    """
    return f"google-gla:{settings.model_name}"


def ensure_api_key() -> None:
    """Ensure ``GOOGLE_API_KEY`` is available in the environment.

    Pydantic AI reads the API key from the ``GOOGLE_API_KEY``
    environment variable automatically. This function copies it
    from the application settings if it's not already set.
    """
    if "GOOGLE_API_KEY" not in os.environ:
        os.environ["GOOGLE_API_KEY"] = settings.google_api_key