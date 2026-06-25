"""
src/tools/code_fixes.py
Deterministic, idiom-guarded Tier-A code remediations.

Each fixer takes a *single source line* and returns a proposed replacement line, or
None if it cannot safely fix it. The guard is the whole point: the scanner's regex
over-matches (it flags `self.debug = True` and `dict(debug=True)` exactly like
`app.run(debug=True)`), so a blind token-flip would corrupt unrelated code. These
transforms re-check the real line and only act on a recognized risky *call* idiom;
anything they don't recognize is declined and stays in the audit report.

Pure functions, no I/O — fully unit-testable. The remediation harness
(src/tools/remediation.py) is responsible for verifying any proposed line actually
clears the finding without breaking the file before it is ever offered to the user.
"""

import re

# Finding types this module can propose deterministic fixes for.
DETERMINISTIC_TYPES = frozenset({"tls_verification_disabled", "debug_enabled"})

# Idiom guards — only transform when the line is a recognized risky call, never a
# bare attribute/dict assignment the scanner happens to match.
_HTTP_CALL_MARKERS = (
    "requests.", "httpx.", "aiohttp", "session.", "urlopen",
    ".get(", ".post(", ".put(", ".patch(", ".delete(", ".head(",
    ".request(", ".send(",
)
_RUN_CALL_MARKERS = (".run(", "uvicorn", "serve(")

_VERIFY_RE = re.compile(r"(verify\s*=\s*)False\b")
_DEBUG_RE = re.compile(r"(debug\s*=\s*)True\b")

_RATIONALES = {
    "tls_verification_disabled": (
        "Re-enable TLS certificate verification (verify=True). Disabling it allows "
        "man-in-the-middle attacks; for an internal CA pass verify='/path/to/ca.pem'."
    ),
    "debug_enabled": (
        "Disable debug mode in the run call (debug=False). Debug mode exposes an "
        "interactive debugger and stack traces; drive it from an env var if needed locally."
    ),
}


def fix_verify_false(line: str) -> str | None:
    """`...verify=False...` -> `...verify=True...`, only inside a recognized HTTP call."""
    if "verify" not in line or "False" not in line:
        return None
    if not any(marker in line for marker in _HTTP_CALL_MARKERS):
        return None  # e.g. `self.verify = False` — not an HTTP call, decline
    new = _VERIFY_RE.sub(r"\g<1>True", line, count=1)
    return new if new != line else None


def fix_debug_true(line: str) -> str | None:
    """`...debug=True...` -> `...debug=False...`, only inside a recognized run() call."""
    if "debug" not in line or "True" not in line:
        return None
    if not any(marker in line for marker in _RUN_CALL_MARKERS):
        return None  # e.g. `self.debug = True` / `dict(debug=True)` — decline
    new = _DEBUG_RE.sub(r"\g<1>False", line, count=1)
    return new if new != line else None


_FIXERS = {
    "tls_verification_disabled": fix_verify_false,
    "debug_enabled": fix_debug_true,
}


def propose_fix(finding_type: str, line: str) -> tuple[str, str] | None:
    """Return (proposed_line, rationale) for a deterministically-fixable finding,
    or None if there is no safe transform for this type/line.

    The caller MUST still verify the proposed line through the remediation harness
    before offering it — this function only proposes, it does not prove safety.
    """
    fixer = _FIXERS.get(finding_type)
    if fixer is None:
        return None
    proposed = fixer(line)
    if proposed is None:
        return None
    return proposed, _RATIONALES[finding_type]
