"""`aoc`: the AgentOps Center command line.

  aoc new-agent claims-helper --template rag   scaffold an agent, sample docs and eval dataset
  aoc validate                                 onboarding checks (run in CI)
  aoc eval --check                             run evals and fail on regression
  aoc register --scores scores.json            import agents into the registry with eval scores
  aoc chaos --tool rag_search --error-rate 0.5 print a fault-injection setting for a gateway
  aoc replay <run-id>                          replay a run through the console API
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from aoc_runtime.config import REPO_ROOT

app = typer.Typer(help=__doc__, no_args_is_help=True, add_completion=False)


@app.command("new-agent")
def new_agent(
    name: Annotated[str, typer.Argument(help="kebab-case agent name, e.g. claims-helper")],
    template: Annotated[str, typer.Option(help="rag | tools")] = "rag",
    owner: Annotated[str, typer.Option(help="contact email for the owning team")] = (
        "team@snackco.example"
    ),
    tenant: Annotated[str, typer.Option(help="tenant id used for cost showback")] = "",
) -> None:
    """Scaffold an agent that is observable and eval-gated from day one."""
    from .scaffold import ScaffoldError, scaffold_agent
    from .validate import validate_agent

    try:
        files = scaffold_agent(name, template, owner=owner, tenant=tenant or f"snackco-{name}")
    except ScaffoldError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(2) from exc
    for f in files:
        typer.echo(f"created {f.relative_to(REPO_ROOT)}")
    problems = validate_agent(REPO_ROOT / "agents" / name.replace("-", "_"))
    if problems:
        typer.secho("scaffold does not validate:", fg=typer.colors.RED, err=True)
        for p in problems:
            typer.echo(f"  - {p}", err=True)
        raise typer.Exit(1)
    typer.secho("validates OK. Next steps:", fg=typer.colors.GREEN)
    typer.echo(f"  1. edit data/docs/{name.replace('-', '_')}/ and evals/datasets/{name}.yaml")
    typer.echo(f"  2. aoc eval --agent {name} --check")
    typer.echo("  3. aoc register, then promote it in the console (draft > staging > prod)")


@app.command()
def validate(
    agents: Annotated[list[str] | None, typer.Argument(help="agent names (default: all)")] = None,
) -> None:
    """Check agents against the onboarding rules. Exits 1 if any agent fails."""
    from .validate import agent_dirs, validate_agent

    dirs = agent_dirs()
    if agents:
        wanted = {a.replace("-", "_") for a in agents}
        dirs = [d for d in dirs if d.name in wanted]
        missing = wanted - {d.name for d in dirs}
        if missing:
            typer.secho(f"unknown agent(s): {sorted(missing)}", fg=typer.colors.RED, err=True)
            raise typer.Exit(2)
    failed = 0
    for d in dirs:
        problems = validate_agent(d)
        if problems:
            failed += 1
            typer.secho(f"FAIL {d.name}", fg=typer.colors.RED)
            for p in problems:
                typer.echo(f"  - {p}")
        else:
            typer.secho(f"PASS {d.name}", fg=typer.colors.GREEN)
    raise typer.Exit(1 if failed else 0)


@app.command("eval")
def run_evals(
    agent: str = "all",
    llm: Annotated[str, typer.Option(help="fake | real")] = "fake",
    baseline: Path = REPO_ROOT / "evals" / "baseline.fake.json",
    out: Path | None = None,
    markdown: Path | None = None,
    check: bool = False,
    update_baseline: bool = False,
) -> None:
    """Run the evals; with --check, exit 1 if a gated metric regressed against the baseline."""
    from evals.runner import main

    argv = ["--agent", agent, "--llm", llm, "--baseline", str(baseline)]
    for flag, value in (("--out", out), ("--markdown", markdown)):
        if value:
            argv += [flag, str(value)]
    argv += (["--check"] if check else []) + (["--update-baseline"] if update_baseline else [])
    raise typer.Exit(main(argv))


@app.command()
def register(
    agent: Annotated[str | None, typer.Option(help="only this agent")] = None,
    scores: Annotated[Path | None, typer.Option(help="scores JSON from `aoc eval --out`")] = None,
    export: Annotated[Path | None, typer.Option(help="write a JSON snapshot per version")] = None,
) -> None:
    """Import agents from the repo into the registry (idempotent), optionally with eval scores."""
    from console import registry
    from console.db import default_session_factory

    score_data = json.loads(scores.read_text(encoding="utf-8")) if scores else {}
    with default_session_factory()() as s:
        try:
            rows = registry.sync_from_repo(s)
        except registry.RegistryError as exc:
            typer.secho(str(exc), fg=typer.colors.RED, err=True)
            raise typer.Exit(1) from exc
        for row in rows:
            if agent and row.agent_name != agent:
                continue
            if row.agent_name in score_data:
                registry.set_eval_scores(s, row.agent_name, row.version, score_data[row.agent_name])
            typer.echo(f"registered {row.agent_name}@{row.version} ({row.status})")
            if export:
                export.mkdir(parents=True, exist_ok=True)
                snapshot = {
                    "agent": row.agent_name, "version": row.version, "status": row.status,
                    "content_hash": row.content_hash, "spec": row.spec,
                    "eval_scores": row.eval_scores,
                }
                target = export / f"{row.agent_name}-{row.version}.json"
                target.write_text(json.dumps(snapshot, indent=2) + "\n", encoding="utf-8")


@app.command()
def chaos(
    tool: Annotated[str, typer.Option(help="tool name, or * for all")] = "*",
    error_rate: float = 0.0,
    latency: Annotated[float, typer.Option(help="added seconds per call")] = 0.0,
    force_loop: bool = False,
    clear: bool = False,
    gateway: Annotated[
        str | None, typer.Option(help="apply to a running gateway (needs AOC_ENABLE_CHAOS_API)")
    ] = None,
    actor: str = "cli",
) -> None:
    """Print an AOC_CHAOS setting, or apply/clear a fault on a running gateway with --gateway."""
    value = "" if clear else json.dumps(
        [{"tool": tool, "error_rate": error_rate, "latency_s": latency, "force_loop": force_loop}]
    )
    if gateway:
        import httpx

        fault = {"tool": tool, "error_rate": error_rate, "latency_s": latency,
                 "force_loop": force_loop}
        if clear:
            r = httpx.delete(f"{gateway}/admin/chaos", params={"actor": actor}, timeout=15)
        else:
            r = httpx.put(f"{gateway}/admin/chaos", json={"faults": [fault], "actor": actor},
                          timeout=15)
        if r.status_code >= 400:
            typer.secho(f"{r.status_code}: {r.text}", fg=typer.colors.RED, err=True)
            raise typer.Exit(1)
        typer.echo(f"gateway faults now: {r.json()['faults']}")
        return
    typer.echo(f"bash:       export AOC_CHAOS='{value}'")
    typer.echo(f"powershell: $env:AOC_CHAOS = '{value}'")


@app.command()
def replay(
    run_id: str,
    mode: Annotated[str, typer.Option(help="deterministic | rerun")] = "deterministic",
    version: Annotated[str | None, typer.Option(help="target version for rerun")] = None,
    url: Annotated[str, typer.Option(help="console API base URL")] = "http://localhost:8001",
    actor: str = "cli",
) -> None:
    """Replay a run through the console API and print the diff summary."""
    import httpx

    r = httpx.post(
        f"{url}/runs/{run_id}/replay",
        json={"mode": mode, "version": version, "actor": actor},
        timeout=60,
    )
    if r.status_code >= 400:
        detail = r.json().get("detail", r.text)
        typer.secho(f"{r.status_code}: {detail}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    body = r.json()
    diff = body["diff"]
    typer.echo(f"new run: {body['run']['run_id']} ({body['run']['status']})")
    typer.echo(f"identical trajectory: {diff['identical']}  answer equal: {diff['answer_equal']}")
    cost, latency = diff["cost_usd"]["delta"], diff["latency_s"]["delta"]
    typer.echo(f"cost delta: {cost:+.6f}  latency delta: {latency:+.3f}s")
    if not diff["identical"]:
        typer.echo(f"first divergence at step {diff['first_divergence']}")


if __name__ == "__main__":
    app()
