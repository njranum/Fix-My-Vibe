"""Tests for the Tier-B/C remediator (src/agents/remediator.py).

Two layers:
  * Contract tests (offline): the remediator's pure logic — query building, KB-context
    assembly (mocked with REAL captured chunks), patch parsing, and the verify→action
    pipeline against the actual vulnerable fixture. This is where Tier-B/C gets its
    offline coverage: recorded patches (good + broken) are replayed through the SAME
    verification harness that gates live output.
  * Live-IQ tests (opt-in): hit the real Azure AI Search index. Skipped via skipif
    unless AZURE_SEARCH_* are set, so CI is green by skip, not by accident.
"""

import os
import json
import shutil
from pathlib import Path

import pytest

from src.agents import remediator
from src.agents import scanner

KB_DIR = Path(__file__).parent / "fixtures" / "kb_responses"


def _load_kb(ftype: str) -> list[dict]:
    return json.loads((KB_DIR / f"{ftype}.json").read_text())


# --------------------------------------------------------------------------- #
# Pure logic
# --------------------------------------------------------------------------- #

def test_primary_stack_prefers_specific_framework():
    assert remediator.primary_stack(["python", "fastapi"]) == "fastapi"
    assert remediator.primary_stack(["node", "react"]) == "react"
    assert remediator.primary_stack([]) == "python"


def test_build_kb_query_maps_threat_filter():
    q, stack, threat = remediator.build_kb_query("sql_injection", "fastapi")
    assert threat == "injection"
    assert stack == "fastapi"
    assert "parameter" in q.lower()
    assert remediator.build_kb_query("hardcoded_secret", "python")[2] == "secrets"


def test_fetch_kb_context_queries_per_type_and_keys_by_type():
    calls = []

    def fake_kb_search(query, stack_filter=None, threat_filter=None):
        calls.append((query, stack_filter, threat_filter))
        # echo a deterministic shape based on threat
        return {"results": [{"title": f"src-{threat_filter}", "url": "u", "content": "c",
                             "threats": [threat_filter], "stacks": [stack_filter]}]}

    ctx = remediator.fetch_kb_context({"sql_injection", "hardcoded_secret"}, "fastapi", fake_kb_search)
    assert set(ctx) == {"sql_injection", "hardcoded_secret"}
    assert len(calls) == 2  # one query per distinct type
    assert all(stack == "fastapi" for _, stack, _ in calls)


def test_fetch_kb_context_survives_kb_error():
    def boom(*a, **k):
        raise RuntimeError("KB down")
    ctx = remediator.fetch_kb_context({"sql_injection"}, "python", boom)
    assert ctx["sql_injection"] == []


def test_parse_patches_accepts_dict_and_list_and_junk():
    assert remediator.parse_patches('{"patches": [{"file": "a"}]}') == [{"file": "a"}]
    assert remediator.parse_patches('[{"file": "b"}]') == [{"file": "b"}]
    assert remediator.parse_patches("not json at all") == []


# --------------------------------------------------------------------------- #
# verify_and_build_actions — Tier-B/C offline coverage via recorded patches
# --------------------------------------------------------------------------- #

@pytest.fixture
def project(tmp_path, fixtures_dir):
    dest = tmp_path / "project"
    shutil.copytree(fixtures_dir / "vulnerable-project", dest)
    return dest


def _scan(project):
    return scanner.run({"project_path": str(project)})


def test_good_sql_patch_becomes_verified_action(project):
    scan = _scan(project)
    # A correct, recorded patch for the planted SQL injection (main.py:24).
    patch = {
        "file": "app/main.py", "line": 25, "finding_type": "sql_injection",
        "original": "    cursor.execute(f\"SELECT * FROM orders WHERE customer = '{customer_name}'\")",
        "proposed": "    cursor.execute(\"SELECT * FROM orders WHERE customer = ?\", (customer_name,))",
        "rationale": "Parameterised query.", "confidence": "high",
    }
    kb_ctx = {"sql_injection": _load_kb("sql_injection")}
    actions, _ = remediator.verify_and_build_actions(scan, [patch], kb_ctx, start_rank=1)
    assert len(actions) == 1
    a = actions[0]
    assert a["action"] == "remediate" and a["finding_type"] == "sql_injection"
    assert a["verification"]["ok"] is True
    assert a["kb_citations"] and a["kb_citations"][0]["url"]  # grounded + cited
    assert a["tier"] == "assisted"


def test_secret_patch_always_gets_rotation_followup(project):
    scan = _scan(project)
    patch = {
        "file": "app/main.py", "line": 14, "finding_type": "hardcoded_secret",
        "original": 'OPENAI_API_KEY = "sk-FAKEFIXTURE9a8b7c6d5e4f3a2b1c0d9e8f7a6b"',
        "proposed": 'OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]',
        "rationale": "Load from env.", "requires_followup": None, "confidence": "high",
    }
    kb_ctx = {"hardcoded_secret": _load_kb("hardcoded_secret")}
    actions, _ = remediator.verify_and_build_actions(scan, [patch], kb_ctx, start_rank=1)
    assert len(actions) == 1
    assert actions[0]["tier"] == "manual"
    assert "rotate" in actions[0]["requires_followup"].lower()  # never silently "fixed"


def test_broken_patch_is_dropped(project):
    scan = _scan(project)
    bad = {
        "file": "app/main.py", "line": 25, "finding_type": "sql_injection",
        "original": "    cursor.execute(f\"SELECT * FROM orders WHERE customer = '{customer_name}'\")",
        "proposed": "    cursor.execute(\"SELECT ... ?\", (customer_name,)",  # missing close paren
        "rationale": "broken", "confidence": "low",
    }
    actions, _ = remediator.verify_and_build_actions(scan, [bad], {"sql_injection": []}, 1)
    assert actions == []  # syntax error → rejected by the harness


def test_stale_original_is_dropped(project):
    scan = _scan(project)
    stale = {
        "file": "app/main.py", "line": 25, "finding_type": "sql_injection",
        "original": "this line is not in the file",
        "proposed": "whatever",
        "confidence": "high",
    }
    actions, _ = remediator.verify_and_build_actions(scan, [stale], {"sql_injection": []}, 1)
    assert actions == []


def test_non_remediable_type_ignored(project):
    scan = _scan(project)
    patch = {"file": "app/main.py", "line": 24, "finding_type": "debug_enabled",
             "original": "x", "proposed": "y"}
    actions, _ = remediator.verify_and_build_actions(scan, [patch], {}, 1)
    assert actions == []  # Tier A is not the remediator's job


# --------------------------------------------------------------------------- #
# Live IQ — opt-in, real Azure AI Search
# --------------------------------------------------------------------------- #

_HAS_SEARCH = bool(os.getenv("AZURE_SEARCH_ENDPOINT") and os.getenv("AZURE_SEARCH_KEY"))


@pytest.mark.foundry
@pytest.mark.skipif(not _HAS_SEARCH, reason="AZURE_SEARCH_ENDPOINT/KEY not set")
@pytest.mark.parametrize("ftype", sorted(remediator.REMEDIABLE_TYPES))
def test_live_kb_returns_relevant_chunks(ftype):
    """The live index returns usable grounding for every remediable finding type."""
    from src.agents.researcher import kb_search
    query, stack_filter, threat_filter = remediator.build_kb_query(ftype, "fastapi")
    result = kb_search(query, stack_filter, threat_filter)
    assert "error" not in result, result.get("error")
    assert result["results"], f"no KB chunks for {ftype}"
    blob = " ".join(r.get("content", "") for r in result["results"]).lower()
    assert len(blob) > 50  # got real content to ground a fix on
