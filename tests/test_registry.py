import pytest

from aoc_runtime.config import REPO_ROOT
from aoc_runtime.spec import load_spec
from console import registry
from console.db import memory_session_factory


@pytest.fixture
def session():
    with memory_session_factory()() as s:
        yield s


def _spec(prompt_text=None, version=None, tools=None):
    spec = load_spec(REPO_ROOT / "agents" / "hr_policy_bot")
    updates = {}
    if prompt_text is not None:
        updates["prompt_text"] = prompt_text
    if version:
        updates["version"] = version
    if tools:
        updates["tools"] = tools
    return spec.model_copy(update=updates)


def test_register_is_idempotent_and_versions_are_immutable(session):
    a = registry.register_version(session, _spec())
    b = registry.register_version(session, _spec())
    assert a.id == b.id and a.status == "draft"
    with pytest.raises(registry.RegistryError, match="bump the version"):
        registry.register_version(session, _spec(prompt_text="a different prompt"))


def test_sync_registers_every_agent_in_repo(session):
    rows = registry.sync_from_repo(session)
    assert {r.agent_name for r in rows} == {"hr-policy-bot", "supply-chain-assistant"}


def test_promotion_path_and_single_prod_version(session):
    registry.register_version(session, _spec())
    registry.register_version(session, _spec(prompt_text="v1.1 prompt", version="1.1.0"))

    with pytest.raises(registry.RegistryError, match="staging before prod"):
        registry.promote(session, "hr-policy-bot", "1.0.0", "prod", "yash")

    registry.promote(session, "hr-policy-bot", "1.0.0", "staging", "yash")
    registry.promote(session, "hr-policy-bot", "1.0.0", "prod", "yash")
    assert registry.served_version(session, "hr-policy-bot").version == "1.0.0"

    registry.promote(session, "hr-policy-bot", "1.1.0", "staging", "yash")
    registry.promote(session, "hr-policy-bot", "1.1.0", "prod", "yash", note="new prompt")
    assert registry.served_version(session, "hr-policy-bot").version == "1.1.0"
    old = registry.get_version(session, "hr-policy-bot", "1.0.0")
    assert old.status == "retired"


def test_rollback_restores_previous_prod_and_records_history(session):
    registry.register_version(session, _spec())
    registry.register_version(session, _spec(prompt_text="v1.1", version="1.1.0"))
    for v in ("1.0.0", "1.1.0"):
        registry.promote(session, "hr-policy-bot", v, "staging", "yash")
        registry.promote(session, "hr-policy-bot", v, "prod", "yash")

    dep = registry.rollback(session, "hr-policy-bot", actor="oncall", note="bad prompt")
    assert (dep.version, dep.action) == ("1.0.0", "rollback")
    assert registry.served_version(session, "hr-policy-bot").version == "1.0.0"
    assert registry.get_version(session, "hr-policy-bot", "1.1.0").status == "retired"

    history = registry.deployments(session, "hr-policy-bot")
    assert [d.action for d in history][:1] == ["rollback"]
    assert len(history) == 5


def test_rollback_errors(session):
    registry.register_version(session, _spec())
    with pytest.raises(registry.RegistryError, match="no prod version"):
        registry.rollback(session, "hr-policy-bot", actor="x")
    registry.promote(session, "hr-policy-bot", "1.0.0", "staging", "x")
    registry.promote(session, "hr-policy-bot", "1.0.0", "prod", "x")
    with pytest.raises(registry.RegistryError, match="no earlier"):
        registry.rollback(session, "hr-policy-bot", actor="x")


def test_snapshot_round_trips_to_an_equivalent_spec(session):
    row = registry.register_version(session, _spec(prompt_text="custom prompt"))
    spec = registry.spec_of(row)
    assert spec.system_prompt == "custom prompt"
    assert spec.tools == ["rag_search"]
