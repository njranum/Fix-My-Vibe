"""
src/tools/security_scan.py
Code-level security pattern scanner for the Scanner agent (M3).

Detects high-signal patterns that AI coding assistants commonly introduce:
hardcoded secrets, eval/exec, SQL built via string interpolation, disabled
TLS verification, debug mode, shell=True. Report-only — findings feed the
Planner, which generates SECURITY.md (see docs/M3-DECISIONS.md, D1/D2).

Design rule: false positives are worse than misses. Every check is narrow,
guarded by a placeholder denylist, and secret values are redacted in output.
"""

import re
from pathlib import Path


# Directories never worth scanning (mirrors fs_tools._SKIP_DIRS + vendored code)
_SKIP_DIRS = {".git", "node_modules", "__pycache__", "dist", "build", ".next",
              ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox", "venv", ".venv",
              "vendor", "site-packages", ".idea", ".vscode"}

# Only scan real source files
_SOURCE_EXTENSIONS = {".py", ".js", ".ts", ".tsx", ".jsx", ".mjs", ".cjs"}

_MAX_FILE_BYTES = 512 * 1024      # skip generated/minified monsters
_MAX_FINDINGS = 50                # cap output size for the agent context
_SNIPPET_MAX_LEN = 120

# A matched "secret" containing any of these is a placeholder, not a finding
_PLACEHOLDER_MARKERS = (
    "example", "changeme", "change-me", "your-", "your_", "placeholder",
    "dummy", "sample", "xxx", "todo", "fixme", "<", "$", "{", "%",
    "os.environ", "getenv", "process.env", "import.meta.env",
)

# Known credential formats — these are high confidence on format alone
_KNOWN_KEY_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("OpenAI/Anthropic/Stripe-style key", re.compile(r"\bsk-[A-Za-z0-9_-]{16,}")),
    ("AWS access key ID",                 re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("GitHub token",                      re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("Slack token",                       re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}")),
    ("Google API key",                    re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
]

# Generic `api_key = "literal"` style assignment with a long opaque value.
# \w* prefix so DB_PASSWORD / client_secret / openai_api_key all match
# (\b alone fails across underscores: "db_password" has no boundary before "password").
_GENERIC_SECRET = re.compile(
    r"(?i)\b(\w*(?:api[_-]?key|secret[_-]?key|secret|auth[_-]?token|access[_-]?token"
    r"|password|passwd))\s*[:=]\s*[\"']([^\"']{12,})[\"']"
)

# eval(/exec( as a call — lookbehind blocks `model.eval()` and `myeval(`
_EVAL_EXEC = re.compile(r"(?<![\w.])(eval|exec)\s*\(")

# SQL keywords inside an interpolated string
_SQL_FSTRING = re.compile(
    r"f[\"'].*\b(SELECT|INSERT|UPDATE|DELETE)\b.*\{", re.IGNORECASE
)
_SQL_TEMPLATE_LITERAL = re.compile(
    r"`[^`]*\b(SELECT|INSERT|UPDATE|DELETE)\b[^`]*\$\{", re.IGNORECASE
)
_SQL_CONCAT = re.compile(
    r"[\"'].*\b(SELECT|INSERT|UPDATE|DELETE)\b.*[\"']\s*\+\s*\w", re.IGNORECASE
)

_VERIFY_FALSE = re.compile(r"\bverify\s*=\s*False\b")
_DEBUG_TRUE = re.compile(r"\bdebug\s*=\s*True\b")
_SHELL_TRUE = re.compile(r"\bshell\s*=\s*True\b")

# Lines carrying an explicit suppression are intentional — skip them
_SUPPRESSED = re.compile(r"#\s*(nosec|noqa)|//\s*nolint", re.IGNORECASE)


def _redact(value: str) -> str:
    """Show enough of a secret to locate it, never enough to use it."""
    if len(value) <= 8:
        return value[:2] + "…"
    return value[:6] + "…" + f"({len(value)} chars)"


def _is_placeholder(value: str) -> bool:
    low = value.lower()
    if any(marker in low for marker in _PLACEHOLDER_MARKERS):
        return True
    # all-same-character strings ("aaaaaaaaaaaa") are test values
    if len(set(low)) <= 2:
        return True
    return False


def _in_string_at(line: str, pos: int) -> bool:
    """True if position `pos` in `line` falls inside a quoted string literal.

    Walks the line tracking quote state ("", '', ``) with escape handling.
    Used to suppress code-pattern checks (eval, verify=False, …) when the
    "code" is actually prose inside a string — e.g. a tool description that
    says "disables verify=False". Without this the scanner flags itself.
    """
    quote: str | None = None
    i = 0
    while i < pos:
        c = line[i]
        if c == "\\":
            i += 2
            continue
        if quote is None and c in "\"'`":
            quote = c
        elif c == quote:
            quote = None
        i += 1
    return quote is not None


def _snippet(line: str, redact_value: str | None = None) -> str:
    text = line.strip()
    if redact_value:
        text = text.replace(redact_value, _redact(redact_value))
    if len(text) > _SNIPPET_MAX_LEN:
        text = text[:_SNIPPET_MAX_LEN] + "…"
    return text


def _check_line(line: str, is_python: bool) -> list[dict]:
    """Run all pattern checks against one source line. Returns 0..n findings."""
    if _SUPPRESSED.search(line):
        return []
    stripped = line.lstrip()
    if stripped.startswith("#") or stripped.startswith("//") or stripped.startswith("*"):
        return []

    findings: list[dict] = []

    # 1. Known credential formats
    for label, pattern in _KNOWN_KEY_PATTERNS:
        m = pattern.search(line)
        if m and not _is_placeholder(m.group(0)):
            findings.append({
                "type": "hardcoded_secret",
                "severity": "high",
                "description": f"Hardcoded {label} in source",
                "snippet": _snippet(line, m.group(0)),
                "recommendation": "Move to an environment variable and rotate this credential — it must be considered compromised.",
            })
            break  # one secret finding per line is enough

    # 2. Generic secret assignment (only if no known-format hit already)
    if not findings:
        m = _GENERIC_SECRET.search(line)
        if m and not _is_placeholder(m.group(2)):
            findings.append({
                "type": "hardcoded_secret",
                "severity": "high",
                "description": f"Hardcoded value assigned to '{m.group(1)}'",
                "snippet": _snippet(line, m.group(2)),
                "recommendation": "Load secrets from environment variables (os.environ / process.env), never string literals.",
            })

    # Checks 3 and 5-7 detect *code* patterns — a match inside a string literal
    # is prose talking about the pattern, not the pattern itself. (Checks 1-2 and 4
    # are exempt: secrets and interpolated SQL legitimately live inside strings.)

    # 3. eval / exec
    m = _EVAL_EXEC.search(line)
    if m and not _in_string_at(line, m.start()):
        findings.append({
            "type": "code_injection",
            "severity": "high",
            "description": f"Use of {m.group(1)}() — executes arbitrary code",
            "snippet": _snippet(line),
            "recommendation": "Replace with a safe alternative (ast.literal_eval, JSON.parse, explicit dispatch).",
        })

    # 4. SQL via string interpolation
    sql_hit = (
        _SQL_FSTRING.search(line)
        or (_SQL_TEMPLATE_LITERAL.search(line) if not is_python else None)
        or _SQL_CONCAT.search(line)
    )
    if sql_hit:
        findings.append({
            "type": "sql_injection",
            "severity": "high",
            "description": "SQL statement built with string interpolation",
            "snippet": _snippet(line),
            "recommendation": "Use parameterised queries (placeholders + bound parameters), never interpolate values into SQL.",
        })

    # 5. TLS verification disabled
    m = _VERIFY_FALSE.search(line)
    if m and not _in_string_at(line, m.start()):
        findings.append({
            "type": "tls_verification_disabled",
            "severity": "medium",
            "description": "HTTPS certificate verification disabled (verify=False)",
            "snippet": _snippet(line),
            "recommendation": "Remove verify=False; if a custom CA is needed, pass its bundle via verify='/path/to/ca.pem'.",
        })

    # 6. Debug mode
    m = _DEBUG_TRUE.search(line)
    if m and not _in_string_at(line, m.start()):
        findings.append({
            "type": "debug_enabled",
            "severity": "medium",
            "description": "Debug mode enabled in code (debug=True)",
            "snippet": _snippet(line),
            "recommendation": "Drive debug mode from an environment variable so it is never enabled in production.",
        })

    # 7. shell=True
    m = _SHELL_TRUE.search(line) if is_python else None
    if m and not _in_string_at(line, m.start()):
        findings.append({
            "type": "shell_injection_risk",
            "severity": "medium",
            "description": "subprocess call with shell=True",
            "snippet": _snippet(line),
            "recommendation": "Pass the command as a list without shell=True; if shell features are required, validate inputs with shlex.quote().",
        })

    return findings


def scan_text(content: str, is_python: bool, file_label: str) -> list[dict]:
    """Scan already-read source text. Returns ALL findings (no cap).

    Shared by the directory scanner and the single-file scanner so both apply
    identical line/docstring/suppression logic. Each finding is tagged with
    ``file`` = file_label and ``line``.
    """
    findings: list[dict] = []
    # Track triple-quoted string state across lines (Python docstrings /
    # multi-line strings): a line that *starts* inside one is prose, not code.
    in_triple = False
    for line_no, line in enumerate(content.splitlines(), start=1):
        line_starts_in_triple = in_triple
        if is_python:
            in_triple = (in_triple + line.count('"""') + line.count("'''")) % 2 == 1
        if line_starts_in_triple:
            continue
        for finding in _check_line(line, is_python):
            finding["file"] = file_label
            finding["line"] = line_no
            findings.append(finding)
    return findings


def scan_file(file_path: str, file_label: str | None = None) -> dict:
    """Scan ONE source file with NO finding cap. Used by the remediation harness
    to verify a fix (finding cleared / no new findings) on a single edited file.

    Returns {"file", "findings", "summary"} or {"error": ...}. Unlike
    scan_security_patterns, never truncates — verification needs the complete set.
    """
    p = Path(file_path).resolve()
    if not p.exists() or not p.is_file():
        return {"error": f"Not a file: {file_path}"}
    label = file_label if file_label is not None else p.name
    if p.suffix not in _SOURCE_EXTENSIONS:
        return {"file": label, "findings": [], "summary": "Not a scannable source file."}
    try:
        content = p.read_text(encoding="utf-8", errors="ignore")
    except OSError as e:
        return {"error": str(e)}
    findings = scan_text(content, p.suffix == ".py", label)
    return {
        "file": label,
        "findings": findings,
        "summary": f"{len(findings)} finding(s) in {label}.",
    }


def scan_security_patterns(project_path: str) -> dict:
    """
    Scan source files for security patterns AI assistants commonly introduce.
    Returns {findings: [...], files_scanned, summary}. Report-only — never modifies files.
    """
    path = Path(project_path).resolve()
    if not path.exists():
        return {"error": f"Path does not exist: {project_path}"}
    if not path.is_dir():
        return {"error": f"Path is not a directory: {project_path}"}

    findings: list[dict] = []
    files_scanned = 0
    truncated = False

    for file_path in sorted(path.rglob("*")):
        if file_path.suffix not in _SOURCE_EXTENSIONS or not file_path.is_file():
            continue
        rel = file_path.relative_to(path)
        if any(part in _SKIP_DIRS for part in rel.parts):
            continue
        try:
            if file_path.stat().st_size > _MAX_FILE_BYTES:
                continue
            content = file_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        files_scanned += 1
        for finding in scan_text(content, file_path.suffix == ".py", str(rel)):
            findings.append(finding)
            if len(findings) >= _MAX_FINDINGS:
                truncated = True
                break
        if truncated:
            break

    high = sum(1 for f in findings if f["severity"] == "high")
    medium = sum(1 for f in findings if f["severity"] == "medium")
    if findings:
        summary = (
            f"Scanned {files_scanned} source file(s): {len(findings)} finding(s) "
            f"({high} high, {medium} medium severity)."
        )
        if truncated:
            summary += f" Output capped at {_MAX_FINDINGS} findings."
    else:
        summary = f"Scanned {files_scanned} source file(s): no security patterns detected."

    return {
        "project_path": str(path),
        "files_scanned": files_scanned,
        "findings": findings,
        "summary": summary,
    }
