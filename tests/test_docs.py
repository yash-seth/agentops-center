"""Docs must not rot: links and images resolve, diagrams look well-formed, ADRs are numbered."""

import re
from urllib.parse import unquote

import pytest

from aoc_runtime.config import REPO_ROOT

DOC_FILES = [REPO_ROOT / "README.md", REPO_ROOT / "infra/aks/README.md"] + sorted(
    (REPO_ROOT / "docs").rglob("*.md")
)
LINK = re.compile(r"!?\[[^\]]*\]\(([^)\s]+)\)")


@pytest.mark.parametrize("path", DOC_FILES, ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_relative_links_and_images_resolve(path):
    text = path.read_text(encoding="utf-8")
    for target in LINK.findall(text):
        if target.startswith(("http://", "https://", "mailto:", "#")):
            continue
        file_part = unquote(target.split("#")[0])
        assert (path.parent / file_part).resolve().exists(), f"{path.name}: broken link {target}"


def test_mermaid_blocks_declare_a_diagram_type_and_close_their_subgraphs():
    blocks = []
    for path in DOC_FILES:
        blocks += re.findall(r"```mermaid\n(.*?)```", path.read_text(encoding="utf-8"), re.S)
    assert len(blocks) >= 4
    for block in blocks:
        first = block.strip().splitlines()[0]
        assert first.startswith(("flowchart", "sequenceDiagram", "graph")), first
        opens = len(re.findall(r"^\s*subgraph\b", block, re.M))
        closes = len(re.findall(r"^\s*end\s*$", block, re.M))
        loops = len(re.findall(r"^\s*loop\b", block, re.M))
        assert opens + loops == closes, block[:60]


def test_adrs_are_numbered_contiguously_and_follow_the_template():
    adrs = sorted((REPO_ROOT / "docs/adr").glob("*.md"))
    numbers = [int(p.name[:4]) for p in adrs]
    assert numbers == list(range(1, len(adrs) + 1))
    for adr in adrs:
        text = adr.read_text(encoding="utf-8")
        assert text.startswith(f"# {int(adr.name[:4])}. ")
        for heading in ("**Status:**", "## Context", "## Decision", "## Consequences"):
            assert heading in text, f"{adr.name} is missing {heading}"


def test_readme_indexes_every_doc():
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    for doc in (REPO_ROOT / "docs").glob("*.md"):
        assert f"docs/{doc.name}" in readme, f"README does not mention docs/{doc.name}"
    assert "infra/aks/README.md" in readme and "docs/adr/" in readme


def test_readme_is_honest_about_what_is_and_is_not_verified():
    """Verified claims must point at CI as evidence, and the unrun pieces must stay labelled."""
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert "verified in GitHub Actions" in readme
    assert "not yet run" in readme
    for still_unrun in ("release workflow", "AKS scripts", "docker compose stack"):
        assert still_unrun in readme, still_unrun


def test_license_names_the_author_and_year():
    text = (REPO_ROOT / "LICENSE").read_text(encoding="utf-8")
    assert text.startswith("MIT License") and "Yash Seth" in text


def test_screenshots_are_real_images():
    for name in ("incident-resolved.jpg", "replay-identical.jpg"):
        data = (REPO_ROOT / "docs/img" / name).read_bytes()
        assert data[:3] == b"\xff\xd8\xff" and len(data) > 10_000, name
