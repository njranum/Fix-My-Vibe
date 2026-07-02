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


def test_security_md_kb_grounding():
    """SECURITY.md gains a KB-sourced References section when kb_context is supplied
    (Foundry mode), and omits it otherwise (local mode). This is the deterministic
    Foundry-IQ signal — it renders on every run with findings, independent of whether
    the LLM remediator emitted a cited patch. Summary/Next steps must survive so the
    verifier's expected sections still pass."""
    from src.agents.planner import _generate_security_md

    scan_result = {
        "project_path": "/tmp/x",
        "code_security_findings": [
            {"type": "hardcoded_secret", "file": "app.py", "line": 9, "severity": "high",
             "description": "Hardcoded secret", "snippet": "KEY = '...'",
             "recommendation": "Load from env"},
        ],
    }
    assert "## References" not in _generate_security_md(scan_result)

    kb = {"hardcoded_secret": [{"title": "OWASP - Secrets Management Cheat Sheet", "url": ""}]}
    grounded = _generate_security_md(scan_result, kb_context=kb)
    assert "## References" in grounded
    assert "OWASP - Secrets Management Cheat Sheet" in grounded
    assert "## Summary" in grounded and "## Next steps" in grounded


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


def test_multiple_fixes_same_file_all_apply(tmp_path):
    """Regression: two fixes in one file where the first inserts an import (shifting
    later line numbers) must BOTH apply — the applier relocates by content, not by a
    stale line number. (This is the bug the live e2e caught.)"""
    from src.agents import executor

    f = tmp_path / "app.py"
    f.write_text(
        '"""App."""\n'
        'import sqlite3\n'
        'KEY = "sk-FAKEFIXTUREaaaaaaaaaaaa"\n'
        'PW = "hunter2hunter2hunter2"\n'
    )
    # Two remediations: the first adds `import os` (shifts line numbers), the second
    # targets a line whose number is now stale.
    plan = {"actions": [
        {"rank": 1, "action": "remediate", "file": "app.py", "line": 3,
         "finding_type": "hardcoded_secret",
         "expected_line": 'KEY = "sk-FAKEFIXTUREaaaaaaaaaaaa"',
         "proposed_line": 'KEY = os.environ["KEY"]', "add_imports": ["os"]},
        {"rank": 2, "action": "remediate", "file": "app.py", "line": 4,
         "finding_type": "hardcoded_secret",
         "expected_line": 'PW = "hunter2hunter2hunter2"',
         "proposed_line": 'PW = os.environ["PW"]', "add_imports": ["os"]},
    ]}
    ex = executor.run({"project_path": str(tmp_path), "action_plan": plan},
                      confirm_fn=lambda _p: [1, 2])

    assert not ex["errors"], ex["errors"]
    assert len([e for e in ex["executed"] if e["status"] == "remediated"]) == 2
    out = f.read_text()
    assert 'os.environ["KEY"]' in out and 'os.environ["PW"]' in out
    assert out.count("import os") == 1  # import added once, not duplicated
    compile(out, "app.py", "exec")


def test_foundry_augment_tolerates_remediator_failure(project):
    """_augment_with_remediations adds only the KB-grounded Tier-B/C fixes (via the
    remediator). Tier-A deterministic fixes come from the planner now (see D-P1), so
    _augment must NOT re-add them (that caused duplicate remediations). It must also
    swallow a remediator failure without losing the rest of the plan or breaking ranks.
    """
    from src.orchestrator import _augment_with_remediations

    scan = scanner.run({"project_path": str(project)})
    plan = {"actions": [{"rank": 1, "action": "create", "file": "SECURITY.md",
                         "content": "x", "priority": "high"}]}
    # object() is not a real Foundry client → remediator.run_with_foundry raises,
    # which _augment must swallow, leaving the config plan intact.
    _augment_with_remediations(plan, scan, client=object())

    # No Tier-A (or any) remediations injected here — that's the planner's job.
    assert [a["file"] for a in plan["actions"]] == ["SECURITY.md"]
    assert [a["rank"] for a in plan["actions"]] == list(range(1, len(plan["actions"]) + 1))


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
