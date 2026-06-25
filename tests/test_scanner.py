"""Integration tests for the local Scanner agent (src/agents/scanner.run).

We assert only on signals that do NOT depend on which AI CLIs are installed on
the test machine. PATH-layer detection (layer 2) can add tools like claude_code
on a developer's laptop, so detected_tools is checked as a superset, never for
exact equality.
"""

from src.agents import scanner


def test_scanner_vulnerable_fixture(fixtures_dir):
    result = scanner.run({"project_path": str(fixtures_dir / "vulnerable-project")})

    # Stack detection is config/content driven → deterministic.
    assert "python" in result["detected_stack"]
    assert "fastapi" in result["detected_stack"]

    # Seven planted code-level findings.
    assert len(result["code_security_findings"]) >= 7

    # An unprotected .env with no .gitignore is a high-severity config issue.
    assert any(i["type"] == "exposed_env" for i in result["security_issues"])

    # Code findings or security issues push priority to high.
    assert result["priority"] == "high"

    assert result["diagnosis_summary"]


def test_scanner_node_fixture_stack(fixtures_dir):
    result = scanner.run({"project_path": str(fixtures_dir / "node-typescript")})
    assert "node" in result["detected_stack"]
    assert "typescript" in result["detected_stack"]


def test_scanner_result_shape(fixtures_dir):
    result = scanner.run({"project_path": str(fixtures_dir / "cursor-project")})
    # The contract every downstream agent relies on.
    for key in (
        "detected_tools", "detected_stack", "security_issues",
        "code_security_findings", "missing_configs", "conventions",
        "diagnosis_summary", "priority",
    ):
        assert key in result


def test_scanner_missing_path(tmp_path):
    result = scanner.run({"project_path": str(tmp_path / "absent")})
    # Graceful: empty stack/tools rather than a crash.
    assert result["detected_stack"] == []
