"""Tests for the remediation safety harness (src/tools/remediation.py).

These prove the gates actually gate: the in-memory verifier rejects broken/ineffective
patches BEFORE they're offered, and the on-disk applier refuses stale lines, blocks
traversal, and never clobbers a pristine backup.
"""

from pathlib import Path

import pytest

from src.tools import remediation as rem
from src.tools.security_scan import scan_file


# --- replace_line --------------------------------------------------------------

def test_replace_line_basic():
    text = "a = 1\nb = 2\nc = 3\n"
    out = rem.replace_line(text, 2, "b = 2", "b = 20")
    assert out == "a = 1\nb = 20\nc = 3\n"


def test_replace_line_preserves_missing_trailing_newline():
    text = "x = 1\ny = 2"  # no trailing newline on last line
    out = rem.replace_line(text, 2, "y = 2", "y = 3")
    assert out == "x = 1\ny = 3"


def test_replace_line_refuses_on_content_drift():
    text = "a = 1\nb = 2\n"
    with pytest.raises(ValueError):
        rem.replace_line(text, 2, "b = 999", "b = 20")  # expected != actual


def test_replace_line_out_of_range():
    with pytest.raises(ValueError):
        rem.replace_line("a = 1\n", 5, "a = 1", "a = 2")


def test_replace_line_only_targets_the_numbered_line():
    # Identical lines elsewhere must not be touched — line number disambiguates.
    text = "f(verify=False)\ng()\nf(verify=False)\n"
    out = rem.replace_line(text, 3, "f(verify=False)", "f(verify=True)")
    assert out == "f(verify=False)\ng()\nf(verify=True)\n"


# --- verify_patch (the pre-apply gate) -----------------------------------------

def test_verify_patch_accepts_good_fix():
    original = 'import requests\nrequests.get("u", verify=False)\n'
    patched = 'import requests\nrequests.get("u", verify=True)\n'
    v = rem.verify_patch(original, patched, "tls_verification_disabled", is_python=True)
    assert v["ok"] is True
    assert v["file_parses"] and v["finding_cleared"] and v["no_new_findings"]


def test_verify_patch_rejects_syntax_error():
    original = 'requests.get("u", verify=False)\n'
    patched = 'requests.get("u", verify=True\n'  # missing close paren
    v = rem.verify_patch(original, patched, "tls_verification_disabled", is_python=True)
    assert v["file_parses"] is False
    assert v["ok"] is False


def test_verify_patch_rejects_when_finding_not_cleared():
    original = 'requests.get("u", verify=False)\n'
    patched = 'requests.get("u", verify=False)  # unchanged\n'
    v = rem.verify_patch(original, patched, "tls_verification_disabled", is_python=True)
    assert v["finding_cleared"] is False
    assert v["ok"] is False


def test_verify_patch_rejects_new_finding():
    original = 'requests.get("u", verify=False)\n'
    # "fix" the tls issue but introduce an eval — must be rejected.
    patched = 'requests.get("u", verify=True); eval(x)\n'
    v = rem.verify_patch(original, patched, "tls_verification_disabled", is_python=True)
    assert v["no_new_findings"] is False
    assert v["ok"] is False


def test_verify_patch_non_python_is_report_only():
    v = rem.verify_patch("a", "b", "tls_verification_disabled", is_python=False)
    assert v["ok"] is False
    assert v["file_parses"] is None


# --- apply_code_fix (on disk) --------------------------------------------------

def _src(tmp_path):
    f = tmp_path / "app.py"
    f.write_text('import requests\nrequests.get("u", verify=False)\n')
    return f


def test_apply_code_fix_writes_and_backs_up(tmp_path):
    _src(tmp_path)
    result = rem.apply_code_fix(str(tmp_path), "app.py", 2,
                                'requests.get("u", verify=False)',
                                'requests.get("u", verify=True)')
    assert "error" not in result
    assert result["backed_up"] is True
    assert 'verify=True' in (tmp_path / "app.py").read_text()
    assert Path(result["backup_path"]).read_text().endswith('verify=False)\n')


def test_apply_code_fix_versioned_backup_no_clobber(tmp_path):
    _src(tmp_path)
    (tmp_path / "app.py.bak").write_text("PRISTINE ORIGINAL\n")  # pre-existing backup
    result = rem.apply_code_fix(str(tmp_path), "app.py", 2,
                                'requests.get("u", verify=False)',
                                'requests.get("u", verify=True)')
    assert "error" not in result
    # The pristine .bak must survive; the new backup goes to .bak.1
    assert (tmp_path / "app.py.bak").read_text() == "PRISTINE ORIGINAL\n"
    assert result["backup_path"].endswith(".bak.1")


def test_apply_code_fix_refuses_stale_line(tmp_path):
    _src(tmp_path)
    result = rem.apply_code_fix(str(tmp_path), "app.py", 2,
                                "SOMETHING ELSE ENTIRELY",
                                'requests.get("u", verify=True)')
    assert "error" in result
    assert 'verify=False' in (tmp_path / "app.py").read_text()  # untouched


def test_apply_code_fix_blocks_path_traversal(tmp_path):
    result = rem.apply_code_fix(str(tmp_path), "../escape.py", 1, "x", "y")
    assert "error" in result


def test_apply_code_fix_missing_file(tmp_path):
    result = rem.apply_code_fix(str(tmp_path), "nope.py", 1, "x", "y")
    assert "error" in result


# --- rollback ------------------------------------------------------------------

def test_rollback_restores_original(tmp_path):
    f = _src(tmp_path)
    result = rem.apply_code_fix(str(tmp_path), "app.py", 2,
                                'requests.get("u", verify=False)',
                                'requests.get("u", verify=True)')
    rb = rem.rollback(result["backup_path"], str(f))
    assert "error" not in rb
    assert 'verify=False' in f.read_text()


# --- scan_file (single-file, cap-free entry point) -----------------------------

def test_scan_file_finds_single_finding(tmp_path):
    f = _src(tmp_path)
    result = scan_file(str(f))
    assert any(x["type"] == "tls_verification_disabled" for x in result["findings"])


def test_scan_file_clean_after_fix(tmp_path):
    f = _src(tmp_path)
    f.write_text('import requests\nrequests.get("u", verify=True)\n')
    result = scan_file(str(f))
    assert result["findings"] == []
