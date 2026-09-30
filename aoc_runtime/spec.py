from pathlib import Path

import yaml
from pydantic import BaseModel


class Limits(BaseModel):
    max_steps: int = 6
    cost_budget_usd: float = 0.05
    tool_timeout_s: float = 10


class AgentSpec(BaseModel):
    name: str
    version: str
    owner: str
    tenant: str
    app_id: str
    prompt: str
    prompt_version: str
    model_allowlist: list[str]
    tools: list[str]
    limits: Limits = Limits()
    redaction: dict[str, str] = {}
    root: Path = Path(".")

    @property
    def system_prompt(self) -> str:
        return (self.root / self.prompt).read_text(encoding="utf-8")


def load_spec(agent_dir: Path) -> AgentSpec:
    data = yaml.safe_load((agent_dir / "agent.yaml").read_text(encoding="utf-8"))
    return AgentSpec(**data, root=agent_dir)
