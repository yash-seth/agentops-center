"""`aoc validate`: onboarding checks stricter than the AgentSpec schema.

Catches the mistakes that cause production incidents: a typo'd config key silently ignored, a model
missing from the price table (so its cost would be reported as zero), a tool declared but not
implemented, no eval dataset, no redaction policy.
"""

from __future__ import annotations

import importlib
import re
from pathlib import Path

import yaml

from aoc_runtime.config import REPO_ROOT
from aoc_runtime.cost import PRICE_TABLE
from aoc_runtime.spec import AgentSpec, Limits

SEMVER = re.compile(r"^\d+\.\d+\.\d+$")
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
NAME = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$")
REDACTION_POLICIES = {"pii-strict", "pii-basic"}
TOP_LEVEL_KEYS = set(AgentSpec.model_fields) - {"root", "prompt_text"}
LIMIT_KEYS = set(Limits.model_fields)


def agent_dirs(root: Path | None = None) -> list[Path]:
    return sorted(p.parent for p in (root or REPO_ROOT / "agents").glob("*/agent.yaml"))


def validate_agent(agent_dir: Path, repo_root: Path = REPO_ROOT) -> list[str]:
    """Return a list of problems (empty means the agent is ready to onboard)."""
    errors: list[str] = []
    yaml_path = agent_dir / "agent.yaml"
    if not yaml_path.exists():
        return [f"{agent_dir.name}: missing agent.yaml"]
    try:
        raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        return [f"{agent_dir.name}: agent.yaml is not valid YAML: {exc}"]

    unknown = set(raw) - TOP_LEVEL_KEYS
    if unknown:
        errors.append(f"unknown key(s) in agent.yaml (typo?): {sorted(unknown)}")
    unknown_limits = set(raw.get("limits") or {}) - LIMIT_KEYS
    if unknown_limits:
        errors.append(f"unknown limits key(s): {sorted(unknown_limits)}")

    try:
        spec = AgentSpec(**{k: v for k, v in raw.items() if k in TOP_LEVEL_KEYS}, root=agent_dir)
    except Exception as exc:  # pydantic ValidationError: report every missing/invalid field
        details = [
            ".".join(map(str, e["loc"])) + ": " + e["msg"]
            for e in getattr(exc, "errors", lambda: [])()
        ]
        return errors + [f"invalid spec: {d}" for d in details or [str(exc)]]

    if not NAME.match(spec.name):
        errors.append(f"name {spec.name!r} must be lowercase kebab-case")
    if spec.name.replace("-", "_") != agent_dir.name:
        errors.append(f"folder {agent_dir.name!r} must match name {spec.name!r} (kebab to snake)")
    if not SEMVER.match(spec.version):
        errors.append(f"version {spec.version!r} must be semantic (MAJOR.MINOR.PATCH)")
    if not EMAIL.match(spec.owner):
        errors.append(f"owner {spec.owner!r} must be a contact email")
    if not spec.tenant.strip() or not spec.app_id.strip():
        errors.append("tenant and app_id are required (cost showback and tagging depend on them)")
    if not spec.prompt_version.strip():
        errors.append("prompt_version is required")

    prompt_path = agent_dir / spec.prompt
    if not prompt_path.exists() or not prompt_path.read_text(encoding="utf-8").strip():
        errors.append(f"prompt file {spec.prompt!r} is missing or empty")

    if not spec.model_allowlist:
        errors.append("model_allowlist must not be empty")
    for model in spec.model_allowlist:
        if model not in PRICE_TABLE:
            errors.append(f"model {model!r} is not in the price table (its cost would show as $0)")

    errors += _check_tools(spec, agent_dir)
    lim = spec.limits
    if not 1 <= lim.max_steps <= 20:
        errors.append("limits.max_steps must be between 1 and 20")
    if lim.cost_budget_usd <= 0:
        errors.append("limits.cost_budget_usd must be positive")
    if lim.tool_timeout_s <= 0:
        errors.append("limits.tool_timeout_s must be positive")
    if not 0 <= lim.tool_retries <= 5:
        errors.append("limits.tool_retries must be between 0 and 5")
    if spec.redaction.get("policy") not in REDACTION_POLICIES:
        errors.append(f"redaction.policy must be one of {sorted(REDACTION_POLICIES)}")

    errors += _check_eval_dataset(spec.name, repo_root)
    return errors


def _check_tools(spec: AgentSpec, agent_dir: Path) -> list[str]:
    if not spec.tools:
        return ["tools must not be empty"]
    tools_file = agent_dir / "tools.py"
    if not tools_file.exists():
        return ["tools.py is missing"]
    try:
        module = importlib.import_module(f"agents.{agent_dir.name}.tools")
        implemented = {t.name for t in module.TOOLS}
    except Exception as exc:  # noqa: BLE001
        return [f"tools.py failed to import or has no TOOLS list: {exc}"]
    out = []
    if missing := set(spec.tools) - implemented:
        out.append(f"tools declared in agent.yaml but not implemented: {sorted(missing)}")
    if extra := implemented - set(spec.tools):
        out.append(f"tools implemented but not declared in agent.yaml: {sorted(extra)}")
    return out


def _check_eval_dataset(name: str, repo_root: Path) -> list[str]:
    path = repo_root / "evals" / "datasets" / f"{name}.yaml"
    if not path.exists():
        return [f"no eval dataset at evals/datasets/{name}.yaml (agents must ship evals)"]
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    out = [f"eval dataset section {s!r} is missing or empty"
           for s in ("retrieval", "trajectory", "answers") if not data.get(s)]
    docs = data.get("docs")
    if not docs or not (repo_root / "data" / "docs" / docs).is_dir():
        out.append(f"eval dataset 'docs' must name a folder under data/docs (got {docs!r})")
    return out
