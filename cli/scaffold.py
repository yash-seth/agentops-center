"""`aoc new-agent`: scaffold an agent that is observable, eval-gated and validated from day one."""

from __future__ import annotations

import re
from pathlib import Path

from aoc_runtime.config import REPO_ROOT

TEMPLATES_DIR = REPO_ROOT / "templates" / "agent"
NAME = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$")
TEMPLATES = {"rag": "rag_search", "tools": "rag_search, lookup_status"}


class ScaffoldError(Exception):
    pass


def _render(text: str, values: dict[str, str]) -> str:
    for key, value in values.items():
        text = text.replace("{{" + key + "}}", value)
    return text


def scaffold_agent(
    name: str,
    template: str = "rag",
    *,
    owner: str,
    tenant: str,
    root: Path = REPO_ROOT,
) -> list[Path]:
    """Create the agent, its sample docs and its eval dataset. Returns the files written."""
    if not NAME.match(name):
        raise ScaffoldError(f"name {name!r} must be lowercase kebab-case, e.g. claims-helper")
    if template not in TEMPLATES:
        raise ScaffoldError(f"template must be one of {sorted(TEMPLATES)}")
    module = name.replace("-", "_")
    agent_dir = root / "agents" / module
    if agent_dir.exists():
        raise ScaffoldError(f"agents/{module} already exists")

    values = {
        "name": name, "module": module, "owner": owner, "tenant": tenant,
        "title": name.replace("-", " ").title(), "tools": TEMPLATES[template],
    }
    layout = {
        TEMPLATES_DIR / "common" / "agent.yaml.tmpl": agent_dir / "agent.yaml",
        TEMPLATES_DIR / "common" / "prompts" / "v1.md.tmpl": agent_dir / "prompts" / "v1.md",
        TEMPLATES_DIR / template / "tools.py.tmpl": agent_dir / "tools.py",
        TEMPLATES_DIR / "common" / "doc.md.tmpl": root / "data" / "docs" / module / "guide.md",
        TEMPLATES_DIR / "common" / "dataset.yaml.tmpl": (
            root / "evals" / "datasets" / f"{name}.yaml"
        ),
    }
    written = []
    for source, target in layout.items():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(_render(source.read_text(encoding="utf-8"), values), encoding="utf-8")
        written.append(target)
    init = agent_dir / "__init__.py"
    init.write_text("", encoding="utf-8")
    return written + [init]
