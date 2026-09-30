"""Thin HTTP client for the console API, so the UI never touches the database directly."""

from __future__ import annotations

import os

import httpx

BASE_URL = os.environ.get("AOC_CONSOLE_URL", "http://localhost:8001")


class ApiError(Exception):
    pass


def _call(method: str, path: str, **kwargs):
    try:
        r = httpx.request(method, BASE_URL + path, timeout=15, **kwargs)
    except httpx.HTTPError as exc:
        raise ApiError(f"cannot reach the console API at {BASE_URL}: {exc}") from exc
    if r.status_code >= 400:
        detail = r.json().get("detail", r.text) if r.headers.get("content-type", "").startswith(
            "application/json"
        ) else r.text
        raise ApiError(str(detail))
    return r.json()


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


def runs(**filters) -> list[dict]:
    return _call("GET", "/runs", params={k: v for k, v in filters.items() if v})


def run(run_id: str) -> dict:
    return _call("GET", f"/runs/{run_id}")


def version_stats() -> list[dict]:
    return _call("GET", "/stats/versions")
