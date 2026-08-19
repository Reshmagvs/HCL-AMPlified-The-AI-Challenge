"""Application settings.

Every tunable lives here and is sourced from the environment (or a local `.env`
file) via pydantic-settings. Nothing else in the codebase may read `os.environ`
directly -- a single typed settings object keeps configuration auditable and
makes tests able to override behaviour by constructing `Settings(...)`.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = BACKEND_DIR.parent
DATA_DIR = BACKEND_DIR / "data"


class Settings(BaseSettings):
    """Typed configuration for the whole backend."""

    model_config = SettingsConfigDict(
        env_file=(REPO_DIR / ".env", BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- LLM ---------------------------------------------------------------
    llm_provider: str = "mock"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.0-flash"
    gemini_embed_model: str = "text-embedding-004"
    embedding_dim: int = 768

    # --- Storage -----------------------------------------------------------
    database_url: str = f"sqlite:///{(REPO_DIR / 'lodestar.db').as_posix()}"

    # --- HTTP --------------------------------------------------------------
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # --- Domain tuning -----------------------------------------------------
    mastery_threshold: float = 0.7
    self_report_cap: float = 0.4
    diagnostic_max_questions: int = 10
    diagnostic_confidence_target: float = 0.75

    # --- Ops ---------------------------------------------------------------
    log_level: str = "INFO"

    @property
    def cors_origin_list(self) -> list[str]:
        """CORS origins as a list, tolerating spaces and a trailing comma."""
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def data_dir(self) -> Path:
        """Directory holding skills.json, courses.json and the .npy matrices."""
        return DATA_DIR


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide singleton so `.env` is parsed exactly once."""
    return Settings()
