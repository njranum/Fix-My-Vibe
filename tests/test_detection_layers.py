"""Tests for layer-2 (PATH) detection and the full 3-layer merge.

These were previously untested because `shutil.which` is machine-dependent. We mock
it so the PATH layer — and crucially the "tool installed but no config file" insight
(detected via PATH → flagged in missing_configs) — is covered deterministically.
"""

import src.tools.fs_tools as fs_tools
from src.tools.fs_tools import check_path_tools
from src.tools.detection import run_full_detection


def _fake_which(present: set[str]):
    """Return a shutil.which stand-in that 'finds' only the given command names."""
    return lambda cmd: f"/usr/local/bin/{cmd}" if cmd in present else None


# --- layer 2: check_path_tools -------------------------------------------------

def test_path_detects_claude(monkeypatch):
    monkeypatch.setattr(fs_tools.shutil, "which", _fake_which({"claude"}))
    assert check_path_tools()["path_tools"] == {"claude_code": "claude"}


def test_path_detects_multiple(monkeypatch):
    monkeypatch.setattr(fs_tools.shutil, "which", _fake_which({"claude", "cursor", "aider"}))
    assert check_path_tools()["path_tools"] == {
        "claude_code": "claude", "cursor": "cursor", "aider": "aider",
    }


def test_path_detects_none(monkeypatch):
    monkeypatch.setattr(fs_tools.shutil, "which", _fake_which(set()))
    assert check_path_tools()["path_tools"] == {}


# --- full 3-layer merge --------------------------------------------------------

def test_claude_on_path_but_no_claude_md_is_flagged_missing(tmp_path, monkeypatch):
    """The headline scenario: `claude` installed, project has no CLAUDE.md →
    detected via PATH AND reported in missing_configs."""
    monkeypatch.setattr(fs_tools.shutil, "which", _fake_which({"claude"}))
    result = run_full_detection(str(tmp_path))

    assert "claude_code" in result["detected_tools"]
    assert result["missing_configs"].get("claude_code") == "CLAUDE.md"
    assert "path:claude" in result["tool_evidence"]["claude_code"]["evidence"]


def test_claude_on_path_with_claude_md_not_flagged(tmp_path, monkeypatch):
    """Same tool, but the config exists → detected from both layers, NOT missing."""
    monkeypatch.setattr(fs_tools.shutil, "which", _fake_which({"claude"}))
    (tmp_path / "CLAUDE.md").write_text("# Project\n")
    result = run_full_detection(str(tmp_path))

    assert "claude_code" in result["detected_tools"]
    assert "claude_code" not in result["missing_configs"]
    evidence = result["tool_evidence"]["claude_code"]["evidence"]
    assert "config:CLAUDE.md" in evidence and "path:claude" in evidence


def test_config_only_tool_detected_without_cli(tmp_path, monkeypatch):
    """A tool with a config file but no CLI on PATH is still detected (layer 1)."""
    monkeypatch.setattr(fs_tools.shutil, "which", _fake_which(set()))
    (tmp_path / ".cursorrules").write_text("rules\n")
    result = run_full_detection(str(tmp_path))
    assert "cursor" in result["detected_tools"]


def test_nothing_found_requests_user_prompt(tmp_path, monkeypatch):
    """No config files, nothing on PATH, no VS Code extensions → fall back to asking."""
    monkeypatch.setattr(fs_tools.shutil, "which", _fake_which(set()))
    result = run_full_detection(str(tmp_path))
    assert result["detected_tools"] == []
    assert result["needs_user_prompt"] is True


def test_merge_dedupes_evidence_across_layers(tmp_path, monkeypatch):
    """Cursor present via config + PATH + VS Code extension → one entry, three evidences."""
    monkeypatch.setattr(fs_tools.shutil, "which", _fake_which({"cursor"}))
    (tmp_path / ".cursorrules").write_text("rules\n")
    vs = tmp_path / ".vscode"
    vs.mkdir()
    (vs / "extensions.json").write_text('{"recommendations": ["cursor.cursor"]}')

    result = run_full_detection(str(tmp_path))
    assert result["detected_tools"].count("cursor") == 1
    evidence = result["tool_evidence"]["cursor"]["evidence"]
    assert "path:cursor" in evidence
    assert "vscode:extension" in evidence
    assert any(e.startswith("config:") for e in evidence)
