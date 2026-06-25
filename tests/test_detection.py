"""Tests for config-file based detection (src/tools/fs_tools.scan_directory and
src/tools/detection helpers).

scan_directory is layer 1 only (config-file signatures), so it is deterministic
and independent of which CLIs happen to be installed on the test machine — unlike
the PATH layer, which we deliberately avoid asserting on here.
"""

import json

from src.tools.fs_tools import (
    scan_directory,
    check_vscode_extensions,
    _is_gitignored,
)


def test_detects_python_and_fastapi_from_requirements(tmp_path):
    (tmp_path / "requirements.txt").write_text("fastapi==0.110\nuvicorn\n")
    result = scan_directory(str(tmp_path))
    assert "python" in result["detected_stack"]
    assert "fastapi" in result["detected_stack"]  # content-based signal


def test_detects_cursor_from_config_file(tmp_path):
    (tmp_path / ".cursorrules").write_text("Be concise.\n")
    result = scan_directory(str(tmp_path))
    assert "cursor" in result["detected_tools"]


def test_missing_config_reported_for_detected_tool(tmp_path):
    # Cursor detected via .cursorrules, but its primary signature is .cursorrules,
    # so claude_code (detected via CLAUDE.md) with no CLAUDE.md would be "missing".
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "settings.json").write_text("{}")
    result = scan_directory(str(tmp_path))
    assert "claude_code" in result["detected_tools"]
    # CLAUDE.md (primary signature) is absent → reported missing.
    assert result["missing_configs"].get("claude_code") == "CLAUDE.md"


def test_exposed_env_flagged_without_gitignore(tmp_path):
    (tmp_path / ".env").write_text("SECRET=abc\n")
    result = scan_directory(str(tmp_path))
    types = {i["type"] for i in result["security_issues"]}
    assert "exposed_env" in types


def test_env_not_flagged_when_gitignored(tmp_path):
    (tmp_path / ".env").write_text("SECRET=abc\n")
    (tmp_path / ".gitignore").write_text(".env\n")
    result = scan_directory(str(tmp_path))
    types = {i["type"] for i in result["security_issues"]}
    assert "exposed_env" not in types


def test_cursor_without_cursorignore_flagged(tmp_path):
    (tmp_path / ".cursorrules").write_text("rules\n")
    (tmp_path / ".env").write_text("X=1\n")
    (tmp_path / ".gitignore").write_text(".env\n")
    result = scan_directory(str(tmp_path))
    types = {i["type"] for i in result["security_issues"]}
    assert "missing_cursorignore" in types


def test_exposed_credential_flagged(tmp_path):
    (tmp_path / "id_rsa").write_text("-----BEGIN PRIVATE KEY-----\n")
    result = scan_directory(str(tmp_path))
    types = {i["type"] for i in result["security_issues"]}
    assert "exposed_credential" in types


def test_node_modules_is_skipped(tmp_path):
    nm = tmp_path / "node_modules" / "pkg"
    nm.mkdir(parents=True)
    (nm / ".cursorrules").write_text("noise\n")
    (tmp_path / "package.json").write_text("{}")
    result = scan_directory(str(tmp_path))
    # The .cursorrules buried in node_modules must not trigger cursor detection.
    assert "cursor" not in result["detected_tools"]


def test_vscode_extension_detection(tmp_path):
    vs = tmp_path / ".vscode"
    vs.mkdir()
    (vs / "extensions.json").write_text(
        json.dumps({"recommendations": ["github.copilot", "continue.continue"]})
    )
    result = check_vscode_extensions(str(tmp_path))
    assert "copilot" in result["vscode_tools"]
    assert "continue" in result["vscode_tools"]


def test_vscode_extension_missing_file(tmp_path):
    result = check_vscode_extensions(str(tmp_path))
    assert result["vscode_tools"] == []


def test_missing_directory_returns_error(tmp_path):
    result = scan_directory(str(tmp_path / "nope"))
    assert "error" in result


# --- _is_gitignored matching rules -------------------------------------------

def test_gitignore_exact_match():
    assert _is_gitignored(".env", ".env\n")


def test_gitignore_extension_glob():
    assert _is_gitignored("secrets.pem", "*.pem\n")


def test_gitignore_prefix_glob():
    assert _is_gitignored(".env.local", ".env.*\n")


def test_gitignore_comment_and_blank_ignored():
    assert not _is_gitignored(".env", "# .env\n\n")


def test_gitignore_no_match():
    assert not _is_gitignored(".env", "node_modules/\n*.log\n")
