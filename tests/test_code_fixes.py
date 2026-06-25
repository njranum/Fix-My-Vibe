"""Tests for the idiom-guarded deterministic transforms (src/tools/code_fixes.py).

The guard is the safety-critical part: the scanner over-matches (it flags
`self.debug = True` like `app.run(debug=True)`), so these transforms must DECLINE
anything that isn't a recognized risky call. Pure functions — no I/O.
"""

from src.tools import code_fixes


# --- verify=False --------------------------------------------------------------

def test_fix_verify_false_in_requests_call():
    line = 'resp = requests.get("https://x", verify=False)'
    assert code_fixes.fix_verify_false(line) == 'resp = requests.get("https://x", verify=True)'


def test_fix_verify_false_httpx():
    assert code_fixes.fix_verify_false('httpx.post(url, verify=False)') == 'httpx.post(url, verify=True)'


def test_fix_verify_false_preserves_spacing():
    assert code_fixes.fix_verify_false('session.get(u, verify = False)') == 'session.get(u, verify = True)'


def test_fix_verify_false_declines_attribute_assignment():
    # `self.verify = False` is flagged by the scanner but is NOT an HTTP call.
    assert code_fixes.fix_verify_false("self.verify = False") is None


def test_fix_verify_false_declines_unrelated():
    assert code_fixes.fix_verify_false("verify = False  # a plain flag") is None


# --- debug=True ----------------------------------------------------------------

def test_fix_debug_true_in_run_call():
    line = "uvicorn.run(app, port=8000, debug=True)"
    assert code_fixes.fix_debug_true(line) == "uvicorn.run(app, port=8000, debug=False)"


def test_fix_debug_true_flask():
    assert code_fixes.fix_debug_true("app.run(debug=True)") == "app.run(debug=False)"


def test_fix_debug_true_declines_attribute_assignment():
    # The exact over-match the safety review flagged.
    assert code_fixes.fix_debug_true("self.debug = True") is None


def test_fix_debug_true_declines_dict_literal():
    assert code_fixes.fix_debug_true("cfg = dict(debug=True)") is None


# --- propose_fix dispatch ------------------------------------------------------

def test_propose_fix_returns_line_and_rationale():
    result = code_fixes.propose_fix("tls_verification_disabled",
                                    'requests.get(u, verify=False)')
    assert result is not None
    proposed, rationale = result
    assert "verify=True" in proposed
    assert rationale


def test_propose_fix_unknown_type_returns_none():
    assert code_fixes.propose_fix("sql_injection", "anything") is None


def test_propose_fix_declines_when_transform_declines():
    assert code_fixes.propose_fix("debug_enabled", "self.debug = True") is None


def test_deterministic_types_are_exactly_two():
    assert code_fixes.DETERMINISTIC_TYPES == {"tls_verification_disabled", "debug_enabled"}
