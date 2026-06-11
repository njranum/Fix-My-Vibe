# AI-Introduced Security Patterns — Cross-Stack

Patterns that show up regardless of language or framework when code is written
with heavy AI assistance. Part of the Fix My Vibe knowledge base.

## Hardcoded secrets from prompt context (OWASP A07)

**Pattern:** The single most common AI-introduced issue. Keys pasted into a chat for
"context" get echoed back into generated code, or the model fabricates a plausible-looking
literal where a config lookup belongs.

**Fix:** Secrets only via environment/secret manager. Scan for known key formats
(`sk-`, `AKIA`, `ghp_`, `xox?-`, `AIza`) before every commit. Rotate anything that
ever appeared in source.

## Swallowed exceptions (OWASP A09: Security Logging and Monitoring Failures)

**Pattern:** `except Exception: pass` / empty `catch {}` — generated to "make the error
go away". Failures become silent, including security-relevant ones.

**Fix:** Catch specific exceptions, log with context, re-raise or return a deliberate
error. An empty catch block needs a comment justifying it.

## Outdated or weak cryptography (OWASP A02: Cryptographic Failures)

**Pattern:** AI training data contains years of old tutorials: MD5/SHA1 for passwords,
ECB mode, `random` for tokens.

**Fix:** Passwords: bcrypt/argon2. Tokens: `secrets` module / `crypto.randomBytes`.
Symmetric encryption: a vetted high-level library (e.g. `cryptography`'s Fernet), never
hand-rolled primitives.

## Missing authorisation checks on "internal" endpoints (OWASP A01: Broken Access Control)

**Pattern:** Generated CRUD endpoints check authentication but not ownership —
`GET /orders/{id}` returns any order to any logged-in user.

**Fix:** Every handler that loads a resource by ID must verify the requester is allowed
to access that specific resource, not just that they are logged in.

## Verbose error responses (OWASP A05)

**Pattern:** Returning raw exception text or stack traces to the client — common in
AI-generated `try/except` blocks that do `return {"error": str(e)}`.

**Fix:** Log details server-side; return generic messages and correct status codes to
the client.

## Unpinned dependencies (OWASP A06)

**Pattern:** AI-generated `requirements.txt`/`package.json` with bare names or `latest`,
making builds non-reproducible and silently pulling new majors.

**Fix:** Pin versions (or use lockfiles) and update deliberately with review.
