"""Safety-gate tests for the orchestrator (src/orchestrator.py) in local mode.

The non-negotiable guarantee: the plan phase NEVER writes files, and the apply
phase writes ONLY the actions whose ranks were explicitly confirmed. These tests
exercise that guarantee end to end without Azure.
"""

import shutil

import pytest

from src.orchestrator import run_plan_phase, run_apply_phase
from src.agents import executor


@pytest.fixture
def project(tmp_path, fixtures_dir):
    """A writable copy of the vulnerable fixture (so writes don't touch the repo)."""
    dest = tmp_path / "project"
    shutil.copytree(fixtures_dir / "vulnerable-project", dest)
    return dest


def _file_set(path):
    return {p.relative_to(path).as_posix() for p in path.rglob("*") if p.is_file()}


def test_plan_phase_writes_nothing(project):
    before = _file_set(project)
    plan_phase = run_plan_phase(str(project), mode="local")
    after = _file_set(project)

    assert after == before, "plan phase must not create or modify any files"
    assert plan_phase["mode"] == "local"
    assert plan_phase["plan_result"]["actions"], "expected a non-empty plan"


def test_plan_includes_security_md_for_code_findings(project):
    plan = run_plan_phase(str(project), mode="local")["plan_result"]
    files = {a["file"] for a in plan["actions"]}
    # Code-level findings drive a SECURITY.md audit report (PATH-independent).
    assert "SECURITY.md" in files


def test_apply_writes_only_confirmed_ranks(project):
    plan = run_plan_phase(str(project), mode="local")["plan_result"]
    actions = plan["actions"]

    security_md = next(a for a in actions if a["file"] == "SECURITY.md")
    other_files = {a["file"] for a in actions if a["file"] != "SECURITY.md"}

    before = _file_set(project)
    apply_phase = run_apply_phase(
        str(project), plan, confirmed_ranks=[security_md["rank"]], mode="local"
    )

    written = {e["file"] for e in apply_phase["execution_result"]["executed"]}
    assert written == {"SECURITY.md"}
    assert (project / "SECURITY.md").exists()

    # No other planned file that didn't already exist should have appeared.
    created = _file_set(project) - before - {"SECURITY.md"}
    assert not (created & other_files), f"unconfirmed files were written: {created}"


def test_confirmation_gate_declined_writes_nothing(project):
    plan = run_plan_phase(str(project), mode="local")["plan_result"]
    before = _file_set(project)

    # Simulate the user declining every action.
    result = executor.run(
        {"project_path": str(project), "action_plan": plan},
        confirm_fn=lambda _plan: [],
    )

    assert result["executed"] == []
    assert _file_set(project) == before
