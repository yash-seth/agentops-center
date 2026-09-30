"""Thin HTTP client for the console API, so the UI never touches the database directly."""

from __future__ import annotations

import os

import httpx

BASE_URL = os.environ.get("AOC_CONSOLE_URL", "http://localhost:8001")


class ApiError(Exception):
    pass


def _raw(method: str, path: str, **kwargs) -> httpx.Response:
    try:
        r = httpx.request(method, BASE_URL + path, timeout=30, **kwargs)
    except httpx.HTTPError as exc:
        raise ApiError(f"cannot reach the console API at {BASE_URL}: {exc}") from exc
    if r.status_code >= 400:
        is_json = r.headers.get("content-type", "").startswith("application/json")
        raise ApiError(str(r.json().get("detail", r.text) if is_json else r.text))
    return r


def _call(method: str, path: str, **kwargs):
    return _raw(method, path, **kwargs).json()


# --- registry ---
def agents() -> list[dict]:
    return _call("GET", "/agents")


def versions(agent: str) -> list[dict]:
    return _call("GET", f"/agents/{agent}/versions")


def deployments(agent: str) -> list[dict]:
    return _call("GET", f"/agents/{agent}/deployments")


def promote(agent: str, version: str, environment: str, actor: str, note: str = "") -> dict:
    body = {"environment": environment, "actor": actor, "note": note}
    return _call("POST", f"/agents/{agent}/versions/{version}/promote", json=body)


def rollback(agent: str, actor: str, to_version: str | None = None) -> dict:
    body = {"actor": actor, "to_version": to_version}
    return _call("POST", f"/agents/{agent}/rollback", json=body)


# --- runs and replay ---
def runs(**filters) -> list[dict]:
    return _call("GET", "/runs", params={k: v for k, v in filters.items() if v})


def run(run_id: str) -> dict:
    return _call("GET", f"/runs/{run_id}")


def replay(run_id: str, mode: str, actor: str, version: str | None = None) -> dict:
    body = {"mode": mode, "actor": actor, "version": version or None}
    return _call("POST", f"/runs/{run_id}/replay", json=body)


def replays(run_id: str) -> list[dict]:
    return _call("GET", f"/runs/{run_id}/replays")


def version_stats() -> list[dict]:
    return _call("GET", "/stats/versions")


# --- incidents ---
def incidents(status: str = "") -> list[dict]:
    return _call("GET", "/incidents", params={"status": status} if status else None)


def incident(incident_id: int) -> dict:
    return _call("GET", f"/incidents/{incident_id}")


def incident_action(incident_id: int, action: str, actor: str, **body) -> dict:
    return _call("POST", f"/incidents/{incident_id}/{action}", json={"actor": actor, **body})


# --- finops ---
def finops_summary(group_by: str, days: int) -> dict:
    return _call("GET", "/finops/summary", params={"group_by": group_by, "days": days})


def finops_csv(days: int) -> str:
    return _raw("GET", "/finops/showback.csv", params={"days": days}).text


def finops_whatif(model: str, days: int) -> dict:
    return _call("GET", "/finops/whatif", params={"model": model, "days": days})


def finops_prices() -> list[dict]:
    return _call("GET", "/finops/prices")


# --- audit ---
def audit(action: str = "") -> list[dict]:
    return _call("GET", "/audit", params={"action": action} if action else None)


def audit_verify() -> dict:
    return _call("GET", "/audit/verify")
