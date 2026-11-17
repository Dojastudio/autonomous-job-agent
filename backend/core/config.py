"""
Configuration & Environment settings for the Autonomous Job Finder & Auto-Apply Backend.
Supports Pydantic Settings when installed, with a zero-dependency standard-library
fallback for lightweight environments.
"""
import os
from pathlib import Path
from typing import List

try:
    from pydantic_settings import BaseSettings, SettingsConfigDict
    _HAS_PYDANTIC_SETTINGS = True
except ImportError:
    _HAS_PYDANTIC_SETTINGS = False
    BaseSettings = object  # type: ignore


def _load_dotenv_fallback() -> None:
    """Loads `.env` key-value pairs into os.environ if python-dotenv/pydantic-settings is unavailable."""
    for candidate in (Path(".env"), Path("../.env")):
        if candidate.is_file():
            for raw_line in candidate.read_text(encoding="utf-8").splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


class Settings(BaseSettings):  # type: ignore
    # Supabase
    SUPABASE_URL: str = os.getenv("SUPABASE_URL", "http://localhost:54321")
    SUPABASE_ANON_KEY: str = os.getenv("SUPABASE_ANON_KEY", "placeholder-anon-key")
    SUPABASE_SERVICE_KEY: str = os.getenv("SUPABASE_SERVICE_KEY", "placeholder-service-key")

    # AI Provider Keys
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")
    OPENROUTER_API_KEY: str = os.getenv("OPENROUTER_API_KEY", "")
    CEREBRAS_API_KEY: str = os.getenv("CEREBRAS_API_KEY", "")

    GEMINI_MODEL: str = 'gemini-2.5-flash-lite'
    GROQ_MODEL: str = 'openai/gpt-oss-120b'
    OPENROUTER_MODEL: str = ''
    CEREBRAS_MODEL: str = ''
    # Embeddings
    EMBEDDING_PROVIDER: str = os.getenv("EMBEDDING_PROVIDER", "local")
    EMBEDDING_MODEL_NAME: str = os.getenv("EMBEDDING_MODEL_NAME", "all-MiniLM-L6-v2")
    EMBEDDING_DIMENSIONS: int = int(os.getenv("EMBEDDING_DIMENSIONS", "384"))

    # Worker & Scheduler
    WORKER_AUTO_APPLY_ENABLED: bool = False
    WORKER_SCHEDULE_ENABLED: bool = False
    BACKEND_HOST: str = os.getenv("BACKEND_HOST", "0.0.0.0")
    BACKEND_PORT: int = int(os.getenv("BACKEND_PORT", "8000"))
    PLAYWRIGHT_HEADLESS: bool = os.getenv("PLAYWRIGHT_HEADLESS", "true").lower() == "true"
    PLAYWRIGHT_TIMEOUT_MS: int = int(os.getenv("PLAYWRIGHT_TIMEOUT_MS", "30000"))
    DISCOVERY_INTERVAL_HOURS: int = int(os.getenv("DISCOVERY_INTERVAL_HOURS", "4"))
    STAGE1_MIN_SIMILARITY: float = float(os.getenv("STAGE1_MIN_SIMILARITY", "0.55"))

    # Default ATS Feed Slugs
    GREENHOUSE_COMPANIES: str = os.getenv(
        "GREENHOUSE_COMPANIES",
        "stripe,airbnb,anthropic,scaleai,vercel,supabase,linear,retool",
    )
    LEVER_COMPANIES: str = os.getenv("LEVER_COMPANIES", "palantir,plaid,mistral")
    ASHBY_COMPANIES: str = os.getenv("ASHBY_COMPANIES", "ramp,notion,perplexity,elevenlabs")

    if _HAS_PYDANTIC_SETTINGS:
        model_config = SettingsConfigDict(
            env_file=(".env", "../.env"),
            env_file_encoding="utf-8",
            extra="ignore",
        )
    else:
        def __init__(self, **kwargs: object) -> None:
            _load_dotenv_fallback()
            for k, v in kwargs.items():
                setattr(self, k, v)

    @property
    def greenhouse_slugs(self) -> List[str]:
        return [s.strip() for s in self.GREENHOUSE_COMPANIES.split(",") if s.strip()]

    @property
    def lever_slugs(self) -> List[str]:
        return [s.strip() for s in self.LEVER_COMPANIES.split(",") if s.strip()]

    @property
    def ashby_slugs(self) -> List[str]:
        return [s.strip() for s in self.ASHBY_COMPANIES.split(",") if s.strip()]


settings = Settings()
