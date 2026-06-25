"""Tests for the code-level security pattern scanner (src/tools/security_scan.py).

These are fully deterministic — no Azure, no network, no PATH dependence.
"""

from src.tools.security_scan import scan_security_patterns


def _write(tmp_path, name, content):
    f = tmp_path / name
    f.write_text(content, encoding="utf-8")
    return f


def _types(result):
    return {f["type"] for f in result["findings"]}


def test_hardcoded_known_key_format(tmp_path):
    _write(tmp_path, "app.py", 'OPENAI_API_KEY = "sk-FAKEFIXTURE9a8b7c6d5e4f3a2b1c0d9e8f"\n')
    result = scan_security_patterns(str(tmp_path))
    assert "hardcoded_secret" in _types(result)
    # The secret value must be redacted in the snippet, never echoed in full.
    snippet = result["findings"][0]["snippet"]
    assert "sk-FAKEFIXTURE9a8b7c6d5e4f3a2b1c0d9e8f" not in snippet


def test_generic_secret_assignment(tmp_path):
    _write(tmp_path, "config.py", 'db_password = "hunter2hunter2hunter2"\n')
    result = scan_security_patterns(str(tmp_path))
    assert "hardcoded_secret" in _types(result)


def test_placeholder_secret_is_not_flagged(tmp_path):
    _write(tmp_path, "config.py", 'API_KEY = "your-api-key-here"\n')
    result = scan_security_patterns(str(tmp_path))
    assert "hardcoded_secret" not in _types(result)


def test_secret_from_environment_is_not_flagged(tmp_path):
    _write(tmp_path, "config.py", 'api_key = os.environ["OPENAI_API_KEY"]\n')
    result = scan_security_patterns(str(tmp_path))
    assert result["findings"] == []


def test_eval_is_code_injection(tmp_path):
    _write(tmp_path, "calc.py", "def run(expr):\n    return eval(expr)\n")
    result = scan_security_patterns(str(tmp_path))
    assert "code_injection" in _types(result)


def test_method_named_eval_is_not_flagged(tmp_path):
    # `model.eval()` is a torch idiom, not the builtin — must not match.
    _write(tmp_path, "train.py", "model.eval()\n")
    result = scan_security_patterns(str(tmp_path))
    assert "code_injection" not in _types(result)


def test_sql_fstring_interpolation(tmp_path):
    _write(tmp_path, "db.py", 'cursor.execute(f"SELECT * FROM users WHERE name = \'{name}\'")\n')
    result = scan_security_patterns(str(tmp_path))
    assert "sql_injection" in _types(result)


def test_verify_false_flagged(tmp_path):
    _write(tmp_path, "client.py", 'requests.get("https://x", verify=False)\n')
    result = scan_security_patterns(str(tmp_path))
    assert "tls_verification_disabled" in _types(result)


def test_debug_true_flagged(tmp_path):
    _write(tmp_path, "server.py", "app.run(debug=True)\n")
    result = scan_security_patterns(str(tmp_path))
    assert "debug_enabled" in _types(result)


def test_shell_true_flagged(tmp_path):
    _write(tmp_path, "ops.py", 'subprocess.run("ls", shell=True)\n')
    result = scan_security_patterns(str(tmp_path))
    assert "shell_injection_risk" in _types(result)


def test_comment_line_is_ignored(tmp_path):
    _write(tmp_path, "notes.py", "# example: eval(expr) is dangerous\n")
    result = scan_security_patterns(str(tmp_path))
    assert result["findings"] == []


def test_pattern_inside_docstring_is_ignored(tmp_path):
    # Prose inside a triple-quoted string mentioning verify=False must not flag.
    src = '"""\nThis helper never sets verify=False on requests.\n"""\n'
    _write(tmp_path, "doc.py", src)
    result = scan_security_patterns(str(tmp_path))
    assert "tls_verification_disabled" not in _types(result)


def test_suppression_marker_respected(tmp_path):
    _write(tmp_path, "ok.py", "result = eval(expr)  # nosec\n")
    result = scan_security_patterns(str(tmp_path))
    assert "code_injection" not in _types(result)


def test_non_source_files_skipped(tmp_path):
    _write(tmp_path, "README.md", 'API_KEY = "sk-FAKEFIXTURE9a8b7c6d5e4f3a2b"\n')
    result = scan_security_patterns(str(tmp_path))
    assert result["files_scanned"] == 0
    assert result["findings"] == []


def test_vulnerable_fixture_finds_all_planted_patterns(fixtures_dir):
    """The vulnerable-project fixture has seven planted findings of five types."""
    result = scan_security_patterns(str(fixtures_dir / "vulnerable-project"))
    types = _types(result)
    assert {"hardcoded_secret", "code_injection", "sql_injection",
            "tls_verification_disabled", "debug_enabled",
            "shell_injection_risk"} <= types
    assert len(result["findings"]) >= 7


def test_missing_path_returns_error(tmp_path):
    result = scan_security_patterns(str(tmp_path / "does-not-exist"))
    assert "error" in result
