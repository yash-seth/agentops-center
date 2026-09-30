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

    # Strict environments: keep prompts/answers out of traces and the run store
    aoc_capture_content: bool = True

    # Import the agents in the repo into the registry when the console API starts
    aoc_auto_sync: bool = False

    # Console database (SQLite file by default; Postgres URL in compose/Kubernetes)
    aoc_db_url: str = ""
    # Where a run's trace can be opened. Verify the path against your Phoenix version.
    aoc_trace_url_template: str = "http://localhost:6006/projects/UHJvamVjdDox/traces/{trace_id}"

    def trace_url(self, trace_id: str) -> str:
        return self.aoc_trace_url_template.format(trace_id=trace_id)

    @property
    def providers(self) -> list[str]:
        return [p.strip() for p in self.aoc_llm_providers.split(",") if p.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
