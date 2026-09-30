"""Console API process. Run: uv run --python 3.12 uvicorn console.api.main:app --port 8001"""

from .app import create_app

app = create_app()
