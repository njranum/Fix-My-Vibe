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
import os
import sys
import ast
import shutil
import difflib
import builtins
from pathlib import Path

from src.tools.security_scan import scan_text
from src.tools.fs_tools import _is_safe_path


# --------------------------------------------------------------------------- #
# Undefined-name guard + import insertion
#
# verify_patch's compile() check proves a patch is syntactically valid, but NOT
# that it runs: `os.environ[...]` compiles fine in a file with no `import os`,
# then NameErrors at import time. So we (a) detect names a patch newly leaves
# unresolved (the guard), and (b) add the missing stdlib import so the fix is
# complete. Only a safe allowlist of stdlib modules is ever auto-imported.
# --------------------------------------------------------------------------- #

_SAFE_STDLIB = frozenset({
    "os", "sys", "ast", "re", "json", "shlex", "subprocess", "secrets",
    "hashlib", "hmac", "base64", "pathlib", "logging", "tempfile", "uuid",
    "datetime", "math", "html",
})


def _bound_names(tree: ast.AST) -> set[str]:
    """Names bound somewhere in the module: imports, defs, classes, assignments,
    args, for/with targets, comprehensions, globals. Plus builtins."""
    names: set[str] = set(dir(builtins))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                names.add((a.asname or a.name).split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for a in node.names:
                names.add(a.asname or a.name)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            names.update(node.names)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            names.add(node.id)
    return names


def _unresolved_names(text: str) -> set[str]:
    """Names used (loaded) but not bound anywhere in `text`. Empty on syntax error."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return set()
    bound = _bound_names(tree)
    used = {n.id for n in ast.walk(tree)
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
    return used - bound


def undefined_introduced(original_text: str, patched_text: str) -> set[str]:
    """Names that the patch leaves unresolved that the original did NOT — i.e. the
    edit references something (e.g. `os`) that isn't imported/defined."""
    return _unresolved_names(patched_text) - _unresolved_names(original_text)


def detect_needed_imports(original_text: str, proposed: str) -> list[str]:
    """Safe-stdlib module roots referenced in `proposed` that aren't already
    available in the file (so the fix needs them imported). Conservative: only the
    allowlist, only `<mod>.` attribute access."""
    try:
        bound = _bound_names(ast.parse(original_text))
    except SyntaxError:
        bound = set(dir(builtins))
    roots = set(re.findall(r"\b([a-z_][a-z0-9_]*)\s*\.", proposed))
    return sorted(m for m in roots if m in _SAFE_STDLIB and m not in bound)


def ensure_imports(text: str, modules) -> str:
    """Insert `import <m>` for each module not already imported, after the module
    docstring. Idempotent."""
    if not modules:
        return text
    try:
        bound = _bound_names(ast.parse(text))
    except SyntaxError:
        bound = set()
    to_add = [m for m in modules if m not in bound]
    if not to_add:
        return text
    lines = text.splitlines(keepends=True)
    insert_at = 0
    try:
        tree = ast.parse(text)
        if (tree.body and isinstance(tree.body[0], ast.Expr)
                and isinstance(tree.body[0].value, ast.Constant)
                and isinstance(tree.body[0].value.value, str)):
            insert_at = tree.body[0].end_lineno  # after the docstring
    except SyntaxError:
        pass
    block = "".join(f"import {m}\n" for m in to_add)
    lines.insert(insert_at, block)
    return "".join(lines)


def build_patched(text: str, start_line: int, expected: str, proposed: str,
                  add_imports=()) -> str:
    """The canonical candidate-construction used by BOTH verification and apply, so
    what we prove is exactly what we write: replace the block, then add any imports."""
    patched = replace_block(text, start_line, expected, proposed)
    return ensure_imports(patched, add_imports) if add_imports else patched


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

def replace_block(text: str, start_line: int, expected: str, proposed: str) -> str:
    """Return `text` with the block beginning at `start_line` (1-based, spanning as
    many lines as `expected` has) replaced by `proposed`. `expected`/`proposed` may be
    single- or multi-line (a Tier-B fix can add an `import`).

    Raises ValueError if out of range or the current block doesn't match `expected`
    (per-line, ignoring trailing whitespace) — a mismatch means the file drifted
    since planning, so we refuse rather than edit blind.
    """
    lines = text.splitlines(keepends=True)
    expected_lines = expected.split("\n")
    n = len(expected_lines)
    if start_line < 1 or start_line + n - 1 > len(lines):
        raise ValueError(
            f"block at line {start_line} (+{n}) out of range (file has {len(lines)} lines)"
        )
    current_slice = lines[start_line - 1: start_line - 1 + n]
    current_norm = [ln.rstrip("\n").rstrip() for ln in current_slice]
    if current_norm != [ln.rstrip() for ln in expected_lines]:
        raise ValueError(
            f"block at line {start_line} changed since planning — refusing edit "
            f"(expected {expected_lines!r}, found {current_norm!r})"
        )
    trailing = "\n" if current_slice and current_slice[-1].endswith("\n") else ""
    new_block = "\n".join(proposed.split("\n")) + trailing
    new_lines = lines[: start_line - 1] + [new_block] + lines[start_line - 1 + n:]
    return "".join(new_lines)


def replace_line(text: str, line_no: int, expected_line: str, proposed_line: str) -> str:
    """Single-line convenience wrapper over replace_block (Tier-A fixes)."""
    return replace_block(text, line_no, expected_line.rstrip("\n"), proposed_line.rstrip("\n"))


def locate_block(text: str, expected: str, hint_line: int | None = None) -> int | None:
    """Find the 1-based start line of `expected` (per-line, ignoring trailing
    whitespace) in `text`. Robust to line drift from earlier same-file edits.

    Returns the unique match; if several, the one nearest `hint_line`; None if the
    content isn't present (e.g. already fixed). This is why batched fixes to one file
    work: each edit relocates its block by content instead of trusting a line number
    that an earlier insertion (e.g. `import os`) has shifted.
    """
    lines = text.splitlines()
    exp = [ln.rstrip() for ln in expected.split("\n")]
    n = len(exp)
    matches = [i + 1 for i in range(len(lines) - n + 1)
               if [ln.rstrip() for ln in lines[i:i + n]] == exp]
    if not matches:
        return None
    if len(matches) == 1 or hint_line is None:
        return matches[0]
    return min(matches, key=lambda m: abs(m - hint_line))


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

    # Compiles but would NameError at runtime? (e.g. `os.environ` with no import os)
    no_undefined_names = not undefined_introduced(original_text, patched_text)

    ok = bool(file_parses and finding_cleared and no_new_findings and no_undefined_names)
    return {
        "file_parses": file_parses,
        "finding_cleared": finding_cleared,
        "no_new_findings": no_new_findings,
        "no_undefined_names": no_undefined_names,
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
    start_line: int,
    expected: str,
    proposed: str,
    add_imports=(),
) -> dict:
    """Apply a single confirmed edit (one line or a multi-line block), plus any
    imports the fix needs. Re-reads the target block, content-checks it against
    `expected`, backs up (versioned), then writes. Returns {written, backed_up,
    backup_path, line} or {error}.

    Uses the same `build_patched` as verification, so what was proven safe is exactly
    what gets written. Confirmation and pre-apply verification happen upstream.
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

    # Relocate by content: earlier edits to this file (esp. inserted imports) may
    # have shifted line numbers since planning, so trust the expected content, not
    # the stale line number.
    located = locate_block(original, expected.rstrip("\n"), hint_line=start_line)
    if located is None:
        return {"error": f"expected block not found in {relative_path} (already changed?)"}

    try:
        patched = build_patched(original, located, expected.rstrip("\n"),
                                proposed.rstrip("\n"), add_imports)
    except ValueError as e:
        return {"error": str(e)}

    backup = versioned_backup(target)
    target.write_text(patched, encoding="utf-8")
    return {
        "written": str(target),
        "backed_up": True,
        "backup_path": str(backup),
        "line": located,
    }


def rollback(backup_path: str, target_path: str) -> dict:
    """Restore `target_path` from `backup_path`. Returns {restored} or {error}."""
    backup = Path(backup_path)
    target = Path(target_path)
    if not backup.exists():
        return {"error": f"Backup not found: {backup_path}"}
    shutil.copy2(backup, target)
    return {"restored": str(target), "from": str(backup)}


# --------------------------------------------------------------------------- #
# Undo / rollback across a whole run
# --------------------------------------------------------------------------- #

_BAK_RE = re.compile(r"^(.+?)\.bak(?:\.(\d+))?$")


def _backup_groups(project_path: str) -> dict[Path, list[tuple[int, Path]]]:
    """Map each backed-up target file to its backups, sorted oldest-first.

    Versioned backups are `<file>.bak` (version 0, the pristine pre-run original)
    then `.bak.1`, `.bak.2`… so the version-0 entry is always the file to restore.
    """
    base = Path(project_path).resolve()
    groups: dict[Path, list[tuple[int, Path]]] = {}
    for p in base.rglob("*"):
        if not p.is_file():
            continue
        m = _BAK_RE.match(p.name)
        if not m:
            continue
        target = p.with_name(m.group(1))
        version = int(m.group(2)) if m.group(2) else 0
        groups.setdefault(target, []).append((version, p))
    for baks in groups.values():
        baks.sort()
    return groups


def find_backups(project_path: str) -> dict[str, str]:
    """{target_file: oldest_backup} for every Fix My Vibe backup under the project."""
    return {str(t): str(baks[0][1]) for t, baks in _backup_groups(project_path).items()}


def restore_backups(project_path: str) -> list[str]:
    """Undo a run: restore each target from its oldest (pristine) backup and remove
    all of that target's backup files. Returns the list of restored target paths."""
    restored: list[str] = []
    for target, baks in _backup_groups(project_path).items():
        oldest = baks[0][1]
        try:
            shutil.copy2(oldest, target)
            for _version, p in baks:
                p.unlink()
            restored.append(str(target))
        except OSError:
            continue
    return restored


# --------------------------------------------------------------------------- #
# Diff rendering (terminal)
# --------------------------------------------------------------------------- #

_ANSI = {"red": "\033[31m", "green": "\033[32m", "cyan": "\033[36m",
         "dim": "\033[2m", "reset": "\033[0m"}


def render_diff(patch: str, indent: str = "      ", color: bool | None = None) -> str:
    """Render a unified diff for the terminal: added lines green, removed red, hunk
    headers cyan, context dimmed. Colour auto-enables on a TTY (off when piped, in
    --json, or when NO_COLOR is set)."""
    if color is None:
        color = sys.stdout.isatty() and not os.environ.get("NO_COLOR")

    def paint(text: str, col: str) -> str:
        return f"{_ANSI[col]}{text}{_ANSI['reset']}" if color else text

    out: list[str] = []
    for line in patch.splitlines():
        if line.startswith(("+++", "---")):
            continue
        if line.startswith("@@"):
            out.append(indent + paint(line, "cyan"))
        elif line.startswith("+"):
            out.append(indent + paint(line, "green"))
        elif line.startswith("-"):
            out.append(indent + paint(line, "red"))
        else:
            out.append(indent + paint(line, "dim"))
    return "\n".join(out)
