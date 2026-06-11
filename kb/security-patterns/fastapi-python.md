# AI-Introduced Security Patterns — FastAPI / Python

Curated reference for vulnerabilities AI coding assistants commonly introduce
in Python web services, with fixes. Part of the Fix My Vibe knowledge base.

## SQL injection via f-strings (OWASP A03: Injection)

**Pattern:** AI assistants frequently generate `cursor.execute(f"SELECT … WHERE x = '{value}'")`
because it reads naturally. Any user-controlled value in the f-string is an injection vector.

**Fix:** Parameterised queries, always:
```python
cursor.execute("SELECT * FROM orders WHERE customer = ?", (customer_name,))
```
With SQLAlchemy, use bound parameters (`text("… WHERE x = :v").bindparams(v=value)`) or the ORM.

## eval/exec on user input (OWASP A03: Injection)

**Pattern:** "Calculator" or "dynamic expression" features generated as `eval(expression)`.
Remote code execution if the input is user-controlled.

**Fix:** `ast.literal_eval` for literals; a real expression parser (e.g. `simpleeval`) for math;
explicit dispatch tables for command-style input.

## Hardcoded credentials (OWASP A07: Identification and Authentication Failures)

**Pattern:** AI completions insert literal API keys (`sk-…`, `AKIA…`) or passwords as
module-level constants, often copied from the prompt context.

**Fix:** Environment variables via `os.environ` / pydantic-settings. Any credential that
reached source control must be rotated — treat it as compromised.

## Disabled TLS verification (OWASP A02: Cryptographic Failures)

**Pattern:** `requests.get(url, verify=False)` generated to "fix" certificate errors in
development, then shipped.

**Fix:** Never disable verification. For internal CAs pass `verify="/path/to/ca-bundle.pem"`.

## Debug mode in production (OWASP A05: Security Misconfiguration)

**Pattern:** `uvicorn.run(app, debug=True)` or `app.run(debug=True)` left in the entrypoint.
Exposes stack traces and, with some frameworks, an interactive debugger.

**Fix:** Drive debug from configuration: `debug=os.environ.get("DEBUG", "0") == "1"`.

## Missing input validation on route handlers (OWASP A03)

**Pattern:** Handlers that accept `dict` or raw query strings instead of typed models,
because the AI mirrored a quick example.

**Fix:** Pydantic models for every request body; typed path/query parameters with
constraints (`Query(min_length=…)`, `Path(gt=0)`). FastAPI then validates and documents
automatically.

## shell=True subprocess calls (OWASP A03: Injection)

**Pattern:** `subprocess.run(f"zip out.zip {filename}", shell=True)` — command injection
if `filename` is user-influenced.

**Fix:** Argument lists without shell: `subprocess.run(["zip", "out.zip", filename])`.
If shell features are unavoidable, quote with `shlex.quote()`.
