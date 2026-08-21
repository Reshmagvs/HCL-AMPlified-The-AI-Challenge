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

    # --- Embeddings (local, no API key) ------------------------------------
    # "auto" loads the local transformer when available and falls back to the
    # offline hashing vectoriser; "hashing" forces the fallback.
    embedder: str = "auto"

    # --- Text generation ----------------------------------------------------
    llm_provider: str = "auto"
    ollama_host: str = "http://127.0.0.1:11434"
    # Measured on a four-core CPU with no GPU: 3b decodes at 11 tok/s, 7b at
    # 4.8 with a two-minute load. 3b is the largest model that answers inside
    # the time a learner will wait.
    ollama_model: str = "qwen2.5:3b-instruct"
    ollama_num_ctx: int = 4096
    # 0 means "use physical core count", which beat the logical count in testing.
    ollama_threads: int = 0
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.5-flash-lite"
    gemini_embed_model: str = "gemini-embedding-2"

    # --- Storage -----------------------------------------------------------
    database_url: str = f"sqlite:///{(REPO_DIR / 'lodestar.db').as_posix()}"

    # --- HTTP --------------------------------------------------------------
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # --- Domain tuning -----------------------------------------------------
    mastery_threshold: float = 0.7
    self_report_cap: float = 0.4
    diagnostic_max_questions: int = 8
    diagnostic_confidence_target: float = 0.75

    # --- Open-world expansion ----------------------------------------------
    # Whether a goal outside the curated graph may be built on demand. Turning
    # this off makes the product strictly closed-world again.
    expansion_enabled: bool = True
    # Verified resources to attach per discovered skill.
    expansion_resources_per_skill: int = 3
    # Where discovered subjects are written. Empty means data/generated.
    # Overridden by the test suite so a test run can never touch, or clear,
    # a real installation's discovered subjects -- which it once did.
    generated_dir: str = ""

    # --- Latency budgets ----------------------------------------------------
    # How long the optional prose layer may add to generating a plan. The
    # reason text is computed either way; this only buys phrasing.
    narration_budget_seconds: float = 6.0

    # How long a model may take on a turn the learner is waiting through --
    # an intake reply, a chat answer, resolving a goal at commit time. Every
    # one of those has a deterministic answer already computed, so exceeding
    # the budget costs phrasing, never correctness.
    #
    # This exists because it was measured: unconstrained, a 3B model on this
    # laptop turned a single intake message into four minutes. The budget is
    # in seconds rather than a switch so the same code uses the model freely
    # on hardware that can answer quickly.
    interactive_budget_seconds: float = 25.0

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
