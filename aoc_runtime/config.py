from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=REPO_ROOT / ".env", extra="ignore")

    aoc_env: str = "dev"
    aoc_tenant: str = "snackco-supply"

    aoc_llm_providers: str = "gemini,groq,fake"
    google_api_key: str = ""
    groq_api_key: str = ""
    aoc_gemini_model: str = "gemini-2.5-flash"
    aoc_groq_model: str = "llama-3.3-70b-versatile"
    aoc_ollama_model: str = "qwen2.5:3b"

    otel_exporter_otlp_endpoint: str = "http://localhost:6006"
    aoc_telemetry_enabled: bool = True

    aoc_vector_store: str = "local"
    aoc_embedder: str = "fastembed"
    aoc_pg_dsn: str = "postgresql://aoc:aoc@localhost:5432/aoc"

    @property
    def providers(self) -> list[str]:
        return [p.strip() for p in self.aoc_llm_providers.split(",") if p.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
