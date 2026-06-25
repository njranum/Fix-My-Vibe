"""Tests for file I/O safety and convention inference (src/tools/fs_tools.py).

The path-traversal guard and backup-before-overwrite behaviour are the safety
guarantees the whole tool rests on, so they are covered explicitly here.
"""

from pathlib import Path

from src.tools.fs_tools import (
    write_file,
    verify_file,
    infer_project_conventions,
    _is_safe_path,
)


def test_write_creates_file(tmp_path):
    result = write_file(str(tmp_path), "CLAUDE.md", "# Hello\n")
    assert "error" not in result
    assert (tmp_path / "CLAUDE.md").read_text() == "# Hello\n"
    assert result["backed_up"] is False


def test_write_creates_nested_dirs(tmp_path):
    result = write_file(str(tmp_path), ".github/copilot-instructions.md", "x\n")
    assert "error" not in result
    assert (tmp_path / ".github" / "copilot-instructions.md").exists()


def test_overwrite_creates_backup(tmp_path):
    target = tmp_path / "CLAUDE.md"
    target.write_text("original\n")
    result = write_file(str(tmp_path), "CLAUDE.md", "updated\n")
    assert result["backed_up"] is True
    backup = tmp_path / "CLAUDE.md.bak"
    assert backup.read_text() == "original\n"
    assert target.read_text() == "updated\n"


def test_path_traversal_is_blocked(tmp_path):
    result = write_file(str(tmp_path), "../escape.txt", "nope\n")
    assert "error" in result
    assert not (tmp_path.parent / "escape.txt").exists()


def test_absolute_path_escape_blocked(tmp_path):
    # An absolute path joined onto project_path resolves outside the base.
    result = write_file(str(tmp_path), "/etc/passwd", "nope\n")
    assert "error" in result


def test_is_safe_path_helper(tmp_path):
    base = tmp_path
    assert _is_safe_path(base, base / "a" / "b.txt")
    assert not _is_safe_path(base, base / ".." / "b.txt")


def test_verify_file_detects_missing_sections(tmp_path):
    (tmp_path / "CLAUDE.md").write_text("# Overview\nStack details\n")
    result = verify_file(str(tmp_path), "CLAUDE.md", ["Overview", "Commands"])
    assert result["verified"] is False
    assert "Commands" in result["missing_sections"]


def test_verify_file_passes_when_all_present(tmp_path):
    (tmp_path / "CLAUDE.md").write_text("# Overview\n## Commands\npytest\n")
    result = verify_file(str(tmp_path), "CLAUDE.md", ["Overview", "Commands"])
    assert result["verified"] is True


def test_verify_missing_file(tmp_path):
    result = verify_file(str(tmp_path), "nope.md", ["x"])
    assert result["verified"] is False


def test_infer_conventions_from_pyproject(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        "[tool.pytest.ini_options]\ntestpaths=['tests']\n"
        "[tool.ruff]\nline-length=100\n"
        "[tool.mypy]\nstrict=true\n"
    )
    conv = infer_project_conventions(str(tmp_path))
    assert conv["test_command"] == "pytest"
    assert conv["lint_command"] == "ruff check ."
    assert conv["type_check_command"] == "mypy ."


def test_infer_conventions_package_manager(tmp_path):
    (tmp_path / "pnpm-lock.yaml").write_text("")
    conv = infer_project_conventions(str(tmp_path))
    assert conv["package_manager"] == "pnpm"


def test_infer_conventions_key_directories(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    conv = infer_project_conventions(str(tmp_path))
    assert "src" in conv["key_directories"]
    assert "tests" in conv["key_directories"]
