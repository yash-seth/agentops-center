"""Streamlit Ops Console.

Run: uv run --python 3.12 --extra ui streamlit run console/ui/app.py
Needs the console API (default http://localhost:8001, override with AOC_CONSOLE_URL).
"""

import pandas as pd
import streamlit as st

from console.ui import client

st.set_page_config(page_title="AgentOps Console", layout="wide")
STATUS_ICON = {"ok": "🟢", "error": "🔴", "loop_stopped": "🟠", "circuit_open": "🟣"}
SEVERITY_ICON = {"critical": "🔴", "warning": "🟠", "info": "🔵"}


def guarded(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except client.ApiError as exc:
        st.error(str(exc))
        return None


def table(rows: list[dict], columns: list[str] | None = None) -> None:
    df = pd.DataFrame(rows)
    if columns and not df.empty:
        df = df[[c for c in columns if c in df.columns]]
    st.dataframe(df, hide_index=True, use_container_width=True)


# ---------------------------------------------------------------- overview / registry
def page_overview() -> None:
    st.header("Fleet overview")
    open_incidents = guarded(client.incidents, "open") or []
    if open_incidents:
        st.warning(f"{len(open_incidents)} open incident(s). See the Incidents page.")
    table(guarded(client.agents) or [])
    st.subheader("Health by agent version (from run history)")
    stats = guarded(client.version_stats) or []
    if stats:
        df = pd.DataFrame(stats)
        df["success_rate"] = (df["success_rate"] * 100).round(1)
        st.dataframe(df, hide_index=True, use_container_width=True)
    else:
        st.info("No runs recorded yet.")


def page_registry() -> None:
    st.header("Agent registry")
    agents = guarded(client.agents) or []
    if not agents:
        st.info("No agents registered. POST /registry/sync on the console API to import them.")
        return
    name = st.selectbox("Agent", [a["name"] for a in agents])
    actor = st.text_input("Acting as", value="yash")
    versions = guarded(client.versions, name) or []
    st.subheader("Versions")
    table(versions, ["version", "status", "prompt_version", "content_hash", "tools", "created_at"])

    st.subheader("Promote or roll back")
    cols = st.columns(4)
    version = cols[0].selectbox("Version", [v["version"] for v in versions]) if versions else None
    env = cols[1].selectbox("To environment", ["staging", "prod"])
    if cols[2].button("Promote", disabled=version is None) and guarded(
        client.promote, name, version, env, actor
    ):
        st.success(f"{name} {version} promoted to {env}")
        st.rerun()
    if cols[3].button("Roll back prod") and (dep := guarded(client.rollback, name, actor)):
        st.success(f"prod rolled back to {dep['version']}")
        st.rerun()

    st.subheader("Deployment history")
    table(guarded(client.deployments, name) or [])


# ---------------------------------------------------------------- runs and replay
def page_runs() -> None:
    st.header("Run history")
    cols = st.columns(4)
    agent = cols[0].text_input("Agent")
    version = cols[1].text_input("Version")
    status = cols[2].selectbox("Status", ["", "ok", "error", "loop_stopped"])
    tenant = cols[3].text_input("Tenant")
    runs = guarded(client.runs, agent=agent, version=version, status=status, tenant=tenant) or []
    if not runs:
        st.info("No runs match.")
        return
    df = pd.DataFrame(runs)
    df.insert(0, "", df["status"].map(STATUS_ICON))
    df["replay"] = df["replay_mode"].fillna("")
    st.dataframe(
        df[["", "run_id", "agent", "version", "tenant", "status", "loop_reason", "replay",
            "steps", "latency_s", "cost_usd", "started_at"]],
        hide_index=True, use_container_width=True,
    )
    chosen = st.selectbox("Open run", [r["run_id"] for r in runs])
    if chosen:
        show_run(chosen)


def show_run(run_id: str) -> None:
    run = guarded(client.run, run_id)
    if not run:
        return
    badge = f" (replay of {run['replay_of'][:8]}, {run['replay_mode']})" if run["replay_of"] else ""
    st.subheader(f"Run {run_id[:8]}… {STATUS_ICON.get(run['status'], '')} {run['status']}{badge}")
    m = st.columns(5)
    m[0].metric("Version", f"{run['agent']} {run['version']}")
    m[1].metric("Latency", f"{run['latency_s'] * 1000:.0f} ms")
    m[2].metric("Cost", f"${run['cost_usd']:.6f}")
    m[3].metric("Tokens", f"{run['input_tokens']}/{run['output_tokens']}")
    m[4].metric("Loop", run["loop_reason"] or "none")
    st.link_button("Open trace in Phoenix", run["trace_url"])
    st.markdown(f"**Question:** {run['question']}")
    st.markdown(f"**Answer:** {run['answer']}")
    st.markdown("**Step timeline**")
    for step in run["step_records"]:
        icon = "🧠" if step["kind"] == "llm" else "🔧"
        flag = "" if step["status"] == "ok" else " ❌"
        latency = f" · {step['latency_ms']:.0f} ms" if step["latency_ms"] is not None else ""
        with st.expander(f"{step['idx']}. {icon} {step['kind']}: {step['name']}{latency}{flag}"):
            st.json(step["input"])
            st.code(step["output"] or "(no text)")
    if not run["replay_of"]:
        replay_panel(run)


def replay_panel(run: dict) -> None:
    st.markdown("---")
    st.subheader("Replay and debug")
    st.caption(
        "Deterministic: re-drives this run from its recording, no LLM or tool calls. "
        "Rerun: executes the question live against another version and diffs the result."
    )
    cols = st.columns(4)
    mode = cols[0].selectbox("Mode", ["deterministic", "rerun"], key=f"mode-{run['run_id']}")
    version = cols[1].text_input(
        "Version (rerun; blank = live)", key=f"ver-{run['run_id']}", disabled=mode != "rerun"
    )
    actor = cols[2].text_input("Acting as", value="yash", key=f"actor-{run['run_id']}")
    if cols[3].button("Replay", key=f"go-{run['run_id']}"):
        result = guarded(client.replay, run["run_id"], mode, actor, version)
        if result:
            show_diff(result)
    for r in guarded(client.replays, run["run_id"]) or []:
        st.caption(f"earlier {r['replay_mode']} replay: {r['run_id']} ({r['status']})")


def show_diff(result: dict) -> None:
    diff, new = result["diff"], result["run"]
    if diff["identical"]:
        st.success(f"Identical trajectory. New run {new['run_id'][:8]}… reproduces the original.")
    else:
        st.warning(
            f"Trajectories differ from step {diff['first_divergence']} "
            f"(new run {new['run_id'][:8]}…, version {new['version']})."
        )
    c = st.columns(3)
    c[0].metric("Status", f"{diff['status']['original']} → {diff['status']['replay']}")
    c[1].metric("Cost", f"${diff['cost_usd']['replay']:.6f}", f"{diff['cost_usd']['delta']:+.6f}")
    lat = diff["latency_s"]
    c[2].metric("Latency", f"{lat['replay']:.3f}s", f"{lat['delta']:+.3f}s")
    rows = []
    for row in diff["steps"]:
        left, right = row["original"], row["replay"]
        rows.append(
            {
                "step": row["idx"],
                "match": "✅" if row["match"] else "❌",
                "original": f"{left['kind']}:{left['name']} [{left['status']}]" if left else "—",
                "replay": f"{right['kind']}:{right['name']} [{right['status']}]" if right else "—",
            }
        )
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)


# ---------------------------------------------------------------- incidents
def page_incidents() -> None:
    st.header("Incidents")
    status = st.selectbox("Status", ["", "open", "acknowledged", "resolved"])
    items = guarded(client.incidents, status) or []
    if not items:
        st.info("No incidents. Alerts from Alertmanager open them automatically (POST /alerts).")
        return
    df = pd.DataFrame(items)
    df.insert(0, "", df["severity"].map(SEVERITY_ICON))
    st.dataframe(
        df[["", "id", "title", "severity", "status", "agent", "version", "tool", "firings",
            "run_count", "created_at"]],
        hide_index=True, use_container_width=True,
    )
    chosen = st.selectbox("Open incident", [i["id"] for i in items])
    if chosen:
        show_incident(chosen)


def show_incident(incident_id: int) -> None:
    inc = guarded(client.incident, incident_id)
    if not inc:
        return
    st.subheader(f"#{inc['id']} {SEVERITY_ICON.get(inc['severity'], '')} {inc['title']}")
    st.markdown(
        f"**Status:** {inc['status']} · **Agent:** {inc['agent']} {inc['version']} · "
        f"**Tool:** {inc['tool'] or '-'} · **Runbook:** `{inc['runbook'] or '-'}`"
    )
    if inc["root_cause"]:
        st.success(f"Root cause: {inc['root_cause']}")

    st.markdown("**Exemplar runs** (auto-attached)")
    for r in inc["runs"]:
        st.markdown(f"- `{r['run_id']}`: {r['reason']}")
    if inc["runs"]:
        pick = st.selectbox("Inspect exemplar", [r["run_id"] for r in inc["runs"]])
        with st.expander("Run detail and replay", expanded=False):
            show_run(pick)

    st.markdown("**Timeline**")
    for e in inc["events"]:
        st.markdown(f"- `{e['ts'][11:19]}` **{e['kind']}** ({e['actor']}) {e['message']}")

    actor = st.text_input("Acting as", value="yash", key="inc-actor")
    cols = st.columns(3)
    if inc["status"] == "open" and cols[0].button("Acknowledge"):
        guarded(client.incident_action, inc["id"], "acknowledge", actor)
        st.rerun()
    note = cols[1].text_input("Add note", key="inc-note")
    if cols[1].button("Post note") and note:
        guarded(client.incident_action, inc["id"], "notes", actor, message=note)
        st.rerun()
    if inc["status"] != "resolved":
        cause = cols[2].text_input("Root cause", key="inc-cause")
        if cols[2].button("Resolve") and guarded(
            client.incident_action, inc["id"], "resolve", actor, root_cause=cause
        ):
            st.rerun()


# ---------------------------------------------------------------- finops
def page_finops() -> None:
    st.header("FinOps: showback")
    st.caption(
        "Costs are list-price equivalents: free tiers cost nothing, "
        "but usage is charged as if it did."
    )
    cols = st.columns(2)
    group_by = cols[0].selectbox("Group by", ["tenant", "agent", "model", "day", "version"])
    days = cols[1].slider("Window (days)", 1, 90, 30)
    data = guarded(client.finops_summary, group_by, days)
    if not data:
        return
    m = st.columns(2)
    m[0].metric("Total cost", f"${data['total_cost_usd']:.6f}")
    m[1].metric("Replay overhead (not charged)", f"${data['replay_overhead_usd']:.6f}")
    if data["rows"]:
        df = pd.DataFrame(data["rows"])
        st.bar_chart(df.set_index("key")["cost_usd"])
        df["share"] = (df["share"] * 100).round(1)
        st.dataframe(df, hide_index=True, use_container_width=True)
    else:
        st.info("No runs in this window.")
    csv = guarded(client.finops_csv, days)
    if csv:
        st.download_button("Download showback CSV", csv, "showback.csv", "text/csv")

    st.subheader("What if we routed to another model?")
    prices = guarded(client.finops_prices) or []
    if prices:
        model = st.selectbox("Model", [p["model"] for p in prices])
        w = guarded(client.finops_whatif, model, days)
        if w:
            c = st.columns(3)
            c[0].metric("Current", f"${w['current_cost_usd']:.6f}")
            c[1].metric("If routed to model", f"${w['alt_cost_usd']:.6f}")
            c[2].metric("Savings", f"{w['savings_pct'] * 100:.1f}%")
            st.caption(w["caveat"])


# ---------------------------------------------------------------- audit
def page_audit() -> None:
    st.header("Audit trail")
    verify = guarded(client.audit_verify)
    if verify:
        if verify["ok"]:
            st.success("Hash chain verified: no tampering detected.")
        else:
            st.error(f"Hash chain broken at event {verify['first_bad_event']}.")
    action = st.selectbox("Action", ["", "run", "input_check", "redaction", "replay"])
    events = guarded(client.audit, action) or []
    table(events, ["id", "ts", "action", "decision", "agent", "version", "tenant", "run_id",
                   "detail", "hash"])


PAGES = {
    "Overview": page_overview,
    "Registry": page_registry,
    "Runs": page_runs,
    "Incidents": page_incidents,
    "FinOps": page_finops,
    "Audit": page_audit,
}
choice = st.sidebar.radio("AgentOps Console", list(PAGES))
PAGES[choice]()
