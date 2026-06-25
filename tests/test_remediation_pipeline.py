"""End-to-end remediation tests in local mode (no Azure).

Proves the planner emits only VERIFIED Tier-A fixes, the executor applies only
confirmed ones, the verifier confirms the finding is cleared, and — critically —
fixing the code does not break it or regress the audit report.
"""

import shutil

import pytest

from src.agents import scanner, planner, executor, verifier


@pytest.fixture
def project(tmp_path, fixtures_dir):
    dest = tmp_path / "project"
    shutil.copytree(fixtures_dir / "vulnerable-project", dest)
    return dest


def _plan(project):
    scan = scanner.run({"project_path": str(project)})
    return scan, planner.run({"scan_result": scan, "research": {}})


def test_planner_emits_verified_tier_a_remediations(project):
    _, plan = _plan(project)
    rem = [a for a in plan["actions"] if a.get("action") == "remediate"]
    types = {a["finding_type"] for a in rem}
    # The fixture has a verify=False and a debug=True in real call sites.
    assert types == {"tls_verification_disabled", "debug_enabled"}
    # Every emitted remediation must already be proven safe.
    assert all(a["verification"]["ok"] for a in rem)
    # Tier B/C findings must NOT be emitted as fixes in v1.
    assert not any(a["finding_type"] in {"sql_injection", "code_injection",
                                          "hardcoded_secret", "shell_injection_risk"}
                   for a in rem)


def test_security_md_still_generated(project):
    """Remediation is additive — the audit report must not regress."""
    _, plan = _plan(project)
    assert any(a["file"] == "SECURITY.md" for a in plan["actions"])


def test_apply_only_confirmed_remediations(project):
    _, plan = _plan(project)
    rem = [a for a in plan["actions"] if a.get("action") == "remediate"]
    confirmed = [rem[0]["rank"]]  # tick only the first code fix

    ex = executor.run({"project_path": str(project), "action_plan": plan},
                      confirm_fn=lambda _p: confirmed)
    written = [(e["file"], e["line"]) for e in ex["executed"] if e.get("status") == "remediated"]
    assert written == [(rem[0]["file"], rem[0]["line"])]
    assert not ex["errors"]


def test_fix_clears_finding_and_verifier_passes(project):
    scan, plan = _plan(project)
    rem = [a for a in plan["actions"] if a.get("action") == "remediate"]
    ranks = [a["rank"] for a in rem]

    ex = executor.run({"project_path": str(project), "action_plan": plan},
                      confirm_fn=lambda _p: ranks)
    vr = verifier.run({"project_path": str(project),
                       "execution_result": ex, "action_plan": plan})
    assert vr["overall_pass"]

    rescan = scanner.run({"project_path": str(project)})
    remaining = {f["type"] for f in rescan["code_security_findings"]}
    assert "tls_verification_disabled" not in remaining
    assert "debug_enabled" not in remaining
    # The un-fixed semantic findings are still reported.
    assert "sql_injection" in remaining


def test_declining_writes_nothing(project):
    import os
    _, plan = _plan(project)
    before = {p.name: p.read_text() for p in (project / "app").glob("*.py")}
    ex = executor.run({"project_path": str(project), "action_plan": plan},
                      confirm_fn=lambda _p: [])
    assert ex["executed"] == []
    after = {p.name: p.read_text() for p in (project / "app").glob("*.py")}
    assert before == after
    assert not any(f.endswith(".bak") for f in os.listdir(project / "app"))


def test_fixed_code_still_parses(project):
    """A fix must not break the file — every patched .py must still compile."""
    _, plan = _plan(project)
    rem = [a for a in plan["actions"] if a.get("action") == "remediate"]
    ranks = [a["rank"] for a in rem]
    executor.run({"project_path": str(project), "action_plan": plan},
                 confirm_fn=lambda _p: ranks)

    patched = (project / "app" / "main.py").read_text()
    compile(patched, "main.py", "exec")  # raises SyntaxError if the fix broke it
    assert "verify=True" in patched
    assert "debug=False" in patched


def test_fixed_fixture_tests_still_pass(project):
    """Behavior preservation: the fixture's own test suite must still pass after the
    fix. Skipped if FastAPI isn't installed (the fixture imports it)."""
    pytest.importorskip("fastapi")
    import subprocess
    import sys

    _, plan = _plan(project)
    ranks = [a["rank"] for a in plan["actions"] if a.get("action") == "remediate"]
    executor.run({"project_path": str(project), "action_plan": plan},
                 confirm_fn=lambda _p: ranks)

    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", str(project / "tests")],
        capture_output=True, text=True, cwd=str(project), timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
