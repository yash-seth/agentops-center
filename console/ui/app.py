"""Streamlit Ops Console.

Run: uv run --python 3.12 --extra ui streamlit run console/ui/app.py
Needs the console API (default http://localhost:8001, override with AOC_CONSOLE_URL).
"""

import pandas as pd
import streamlit as st

from console.ui import client

st.set_page_config(page_title="AgentOps Console", layout="wide")
STATUS_ICON = {"ok": "🟢", "error": "🔴", "loop_stopped": "🟠"}


def guarded(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except client.ApiError as exc:
        st.error(str(exc))
        return None


def page_overview() -> None:
    st.header("Fleet overview")
    agents = guarded(client.agents) or []
    st.dataframe(pd.DataFrame(agents), hide_index=True, use_container_width=True)
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
    version_cols = ["version", "status", "prompt_version", "content_hash", "tools", "created_at"]
    st.dataframe(
        pd.DataFrame(versions)[version_cols] if versions else pd.DataFrame(),
        hide_index=True, use_container_width=True,
    )

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
    deps = guarded(client.deployments, name) or []
    st.dataframe(pd.DataFrame(deps), hide_index=True, use_container_width=True)


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
    st.dataframe(
        df[["", "run_id", "agent", "version", "tenant", "status", "loop_reason", "steps",
            "latency_s", "cost_usd", "started_at"]],
        hide_index=True, use_container_width=True,
    )
    chosen = st.selectbox("Open run", [r["run_id"] for r in runs])
    if chosen:
        show_run(chosen)


def show_run(run_id: str) -> None:
    run = guarded(client.run, run_id)
    if not run:
        return
    st.subheader(f"Run {run_id[:8]}… {STATUS_ICON.get(run['status'], '')} {run['status']}")
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


PAGES = {"Overview": page_overview, "Registry": page_registry, "Runs": page_runs}
choice = st.sidebar.radio("AgentOps Console", list(PAGES))
PAGES[choice]()
