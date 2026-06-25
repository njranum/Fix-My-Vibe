"""
src/tools/remediation.py
The safety harness for code-level remediation.

Two responsibilities, kept separate:

  - PROVE a proposed patch is safe BEFORE it is offered to the user
    (`verify_patch`, on in-memory text — never touches disk).
  - APPLY a confirmed patch safely (`apply_code_fix`: re-read the target line,
    content-check it against what was planned, versioned backup, write).

A patch is only ever surfaced if `verify_patch` says: the file still parses, the
finding is cleared, and no NEW findings were introduced. The scanner is a coarse
oracle, so this harness narrows the set — the human diff-confirm gate (planner →
executor) is the final decision. See docs/code-remediation-plan.md §4.
"""

import re
import shutil
import difflib
from pathlib import Path

from src.tools.security_scan import scan_text
from src.tools.fs_tools import _is_safe_path


# --------------------------------------------------------------------------- #
# Finding-set comparison (line-number independent)
# --------------------------------------------------------------------------- #

def _normalize_snippet(text: str) -> str:
    """Collapse whitespace so two renderings of the same code compare equal."""
    return re.sub(r"\s+", " ", (text or "").strip())


def _finding_key(finding: dict) -> tuple[str, str]:
    """Identity of a finding ignoring its line number (edits shift line numbers).

    Keyed on (type, normalized snippet) so a multi-line patch that shifts every
    later finding's line doesn't read as a swarm of 'new' findings.
    """
    return (finding.get("type", ""), _normalize_snippet(finding.get("snippet", "")))


# --------------------------------------------------------------------------- #
# Pure text transforms (no I/O) — used to build candidates and to verify
# --------------------------------------------------------------------------- #

def replace_line(text: str, line_no: int, expected_line: str, proposed_line: str) -> str:
    """Return `text` with line `line_no` (1-based) replaced by `proposed_line`.

    Raises ValueError if the line is out of range or its current content doesn't
    match `expected_line` (compared ignoring trailing whitespace) — that mismatch
    means the file drifted since planning, so we refuse rather than edit blind.
    """
    lines = text.splitlines(keepends=True)
    if line_no < 1 or line_no > len(lines):
        raise ValueError(f"line {line_no} out of range (file has {len(lines)} lines)")
    current = lines[line_no - 1]
    newline = "\n" if current.endswith("\n") else ""
    if current.rstrip() != expected_line.rstrip():
        raise ValueError(
            f"line {line_no} changed since planning — refusing edit "
            f"(expected {expected_line.rstrip()!r}, found {current.rstrip()!r})"
        )
    lines[line_no - 1] = proposed_line.rstrip("\n") + newline
    return "".join(lines)


def make_unified_diff(original: str, patched: str, file_label: str) -> str:
    """Unified diff between two text blobs, for the confirmation prompt."""
    diff = difflib.unified_diff(
        original.splitlines(keepends=True),
        patched.splitlines(keepends=True),
        fromfile=f"a/{file_label}",
        tofile=f"b/{file_label}",
    )
    return "".join(diff)


def _python_parses(text: str) -> bool:
    try:
        compile(text, "<remediation-candidate>", "exec")
        return True
    except SyntaxError:
        return False


def verify_patch(
    original_text: str,
    patched_text: str,
    finding_type: str,
    is_python: bool,
    file_label: str = "<patch>",
) -> dict:
    """Prove a candidate patch is safe — IN MEMORY, before it is offered or applied.

    Returns {file_parses, finding_cleared, no_new_findings, ok}. `ok` is the AND of
    all three. For non-Python files in v1, `file_parses` is None (we have no reliable
    offline parser) and `ok` is False — JS/TS stays report-only.
    """
    if not is_python:
        return {"file_parses": None, "finding_cleared": False,
                "no_new_findings": False, "ok": False,
                "reason": "non-Python remediation is report-only in v1"}

    file_parses = _python_parses(patched_text)

    before = scan_text(original_text, True, file_label)
    after = scan_text(patched_text, True, file_label)

    before_of_type = sum(1 for f in before if f.get("type") == finding_type)
    after_of_type = sum(1 for f in after if f.get("type") == finding_type)
    finding_cleared = after_of_type < before_of_type

    before_keys = {_finding_key(f) for f in before}
    no_new_findings = not any(_finding_key(f) not in before_keys for f in after)

    ok = bool(file_parses and finding_cleared and no_new_findings)
    return {
        "file_parses": file_parses,
        "finding_cleared": finding_cleared,
        "no_new_findings": no_new_findings,
        "ok": ok,
    }


# --------------------------------------------------------------------------- #
# Disk operations — versioned backup, apply, rollback
# --------------------------------------------------------------------------- #

def versioned_backup(target: Path) -> Path:
    """Back up `target` to `<name>.bak`, or `.bak.1`, `.bak.2`… if one exists.

    Unlike fs_tools.write_file (which clobbers `.bak`), this never overwrites an
    existing backup, so a second run can't destroy the pristine original.
    """
    backup = target.with_suffix(target.suffix + ".bak")
    if backup.exists():
        i = 1
        while True:
            candidate = target.with_suffix(target.suffix + f".bak.{i}")
            if not candidate.exists():
                backup = candidate
                break
            i += 1
    shutil.copy2(target, backup)
    return backup


def apply_code_fix(
    project_path: str,
    relative_path: str,
    line_no: int,
    expected_line: str,
    proposed_line: str,
) -> dict:
    """Apply a single confirmed line edit. Re-reads the line, content-checks it,
    backs up (versioned), then writes. Returns {written, backed_up, backup_path,
    line} or {error}.

    Confirmation and pre-apply verification must already have happened upstream;
    this is the low-level writer the executor delegates to.
    """
    base = Path(project_path).resolve()
    target = base / relative_path
    if not _is_safe_path(base, target):
        return {"error": f"Path traversal blocked: {relative_path}"}
    if not target.exists() or not target.is_file():
        return {"error": f"File not found: {relative_path}"}

    try:
        original = target.read_text(encoding="utf-8")
    except OSError as e:
        return {"error": str(e)}

    try:
        patched = replace_line(original, line_no, expected_line, proposed_line)
    except ValueError as e:
        return {"error": str(e)}

    backup = versioned_backup(target)
    target.write_text(patched, encoding="utf-8")
    return {
        "written": str(target),
        "backed_up": True,
        "backup_path": str(backup),
        "line": line_no,
    }


def rollback(backup_path: str, target_path: str) -> dict:
    """Restore `target_path` from `backup_path`. Returns {restored} or {error}."""
    backup = Path(backup_path)
    target = Path(target_path)
    if not backup.exists():
        return {"error": f"Backup not found: {backup_path}"}
    shutil.copy2(backup, target)
    return {"restored": str(target), "from": str(backup)}
