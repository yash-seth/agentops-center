"""Agent registry: immutable versions, promotion through environments, rollback, deploy history.

Lifecycle: draft -> staging -> prod -> retired. Only one version per agent is ``prod`` at a time;
promoting another version retires the current one, and a rollback re-promotes an earlier one.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from aoc_runtime.config import REPO_ROOT
from aoc_runtime.spec import AgentSpec, load_spec

from .db import Agent, AgentVersion, Deployment

ENVIRONMENTS = ("staging", "prod")


class RegistryError(Exception):
    """Invalid registry operation (unknown version, bad transition, changed content)."""


def snapshot_of(spec: AgentSpec) -> dict:
    data = spec.model_dump(mode="json", exclude={"root", "prompt_text"})
    data["prompt_text"] = spec.system_prompt
    return data


def content_hash(snapshot: dict) -> str:
    return hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest()


def register_version(s: Session, spec: AgentSpec) -> AgentVersion:
    """Register a version. Idempotent for identical content; versions are immutable."""
    snapshot = snapshot_of(spec)
    digest = content_hash(snapshot)
    if s.get(Agent, spec.name) is None:
        s.add(Agent(name=spec.name, owner=spec.owner))
    existing = s.scalar(
        select(AgentVersion).where(
            AgentVersion.agent_name == spec.name, AgentVersion.version == spec.version
        )
    )
    if existing is not None:
        if existing.content_hash != digest:
            raise RegistryError(
                f"{spec.name} {spec.version} is already registered with different content; "
                "bump the version instead of editing it"
            )
        return existing
    row = AgentVersion(
        agent_name=spec.name,
        version=spec.version,
        prompt_version=spec.prompt_version,
        content_hash=digest,
        spec=snapshot,
    )
    s.add(row)
    s.commit()
    return row


def sync_from_repo(s: Session, agents_dir: Path | None = None) -> list[AgentVersion]:
    agents_dir = agents_dir or REPO_ROOT / "agents"
    out = []
    for yaml_path in sorted(agents_dir.glob("*/agent.yaml")):
        out.append(register_version(s, load_spec(yaml_path.parent)))
    return out


def get_version(s: Session, agent: str, version: str) -> AgentVersion:
    row = s.scalar(
        select(AgentVersion).where(
            AgentVersion.agent_name == agent, AgentVersion.version == version
        )
    )
    if row is None:
        raise RegistryError(f"unknown version {agent} {version}")
    return row


def list_versions(s: Session, agent: str) -> list[AgentVersion]:
    return list(
        s.scalars(
            select(AgentVersion)
            .where(AgentVersion.agent_name == agent)
            .order_by(AgentVersion.created_at.desc(), AgentVersion.id.desc())
        )
    )


def served_version(s: Session, agent: str, environment: str = "prod") -> AgentVersion | None:
    status = "prod" if environment == "prod" else "staging"
    return s.scalar(
        select(AgentVersion)
        .where(AgentVersion.agent_name == agent, AgentVersion.status == status)
        .order_by(AgentVersion.id.desc())
    )


def spec_of(row: AgentVersion) -> AgentSpec:
    return AgentSpec(**row.spec)


def _record(
    s: Session, row: AgentVersion, env: str, action: str, actor: str, note: str,
    image_digest: str | None = None,
) -> Deployment:
    dep = Deployment(
        agent_name=row.agent_name, version=row.version, environment=env, action=action,
        actor=actor, note=note, image_digest=image_digest,
    )
    s.add(dep)
    s.commit()
    return dep


def promote(
    s: Session, agent: str, version: str, environment: str, actor: str, note: str = "",
    image_digest: str | None = None,
) -> Deployment:
    if environment not in ENVIRONMENTS:
        raise RegistryError(f"environment must be one of {ENVIRONMENTS}")
    row = get_version(s, agent, version)
    if environment == "staging":
        if row.status != "draft":
            raise RegistryError(f"only a draft can move to staging (is {row.status})")
        row.status = "staging"
    else:
        if row.status != "staging":
            raise RegistryError(f"a version must be in staging before prod (is {row.status})")
        _retire_current_prod(s, agent)
        row.status = "prod"
    return _record(s, row, environment, "promote", actor, note, image_digest)


def _retire_current_prod(s: Session, agent: str) -> None:
    for current in s.scalars(
        select(AgentVersion).where(AgentVersion.agent_name == agent, AgentVersion.status == "prod")
    ):
        current.status = "retired"


def rollback(
    s: Session, agent: str, actor: str, to_version: str | None = None, note: str = ""
) -> Deployment:
    """Re-promote an earlier prod version (default: the one that was live before the current)."""
    current = served_version(s, agent, "prod")
    if current is None:
        raise RegistryError(f"{agent} has no prod version to roll back from")
    if to_version is None:
        history = s.scalars(
            select(Deployment)
            .where(Deployment.agent_name == agent, Deployment.environment == "prod")
            .order_by(Deployment.id.desc())
        )
        previous = next((d.version for d in history if d.version != current.version), None)
        if previous is None:
            raise RegistryError(f"{agent} has no earlier prod version to roll back to")
        to_version = previous
    target = get_version(s, agent, to_version)
    if target.version == current.version:
        raise RegistryError("target is already the live version")
    if target.status != "retired":
        raise RegistryError(f"can only roll back to a retired version (is {target.status})")
    current.status = "retired"
    target.status = "prod"
    return _record(s, target, "prod", "rollback", actor, note or f"from {current.version}")


def deployments(s: Session, agent: str, limit: int = 50) -> list[Deployment]:
    return list(
        s.scalars(
            select(Deployment)
            .where(Deployment.agent_name == agent)
            .order_by(Deployment.id.desc())
            .limit(limit)
        )
    )


def set_eval_scores(s: Session, agent: str, version: str, scores: dict) -> AgentVersion:
    row = get_version(s, agent, version)
    row.eval_scores = scores
    s.commit()
    return row
