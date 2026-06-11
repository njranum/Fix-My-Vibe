# AI-Introduced Security Patterns — JavaScript / TypeScript / React

Curated reference for vulnerabilities AI coding assistants commonly introduce
in Node and React/Next.js codebases. Part of the Fix My Vibe knowledge base.

## XSS via dangerouslySetInnerHTML (OWASP A03: Injection)

**Pattern:** AI assistants reach for `dangerouslySetInnerHTML={{ __html: userContent }}`
when asked to "render rich text", injecting unsanitised HTML.

**Fix:** Render text through JSX (auto-escaped). If HTML rendering is genuinely required,
sanitise first with DOMPurify and document why.

## Secrets in client bundles (OWASP A05: Security Misconfiguration)

**Pattern:** API keys placed in front-end code or in `NEXT_PUBLIC_*` / `VITE_*` env vars —
which are compiled into the public JavaScript bundle by design.

**Fix:** Secrets live server-side only (API routes / server components / backend). The
`NEXT_PUBLIC_`/`VITE_` prefixes mean "public by design" — never put credentials there.

## SQL via template literals (OWASP A03: Injection)

**Pattern:** `` db.query(`SELECT … WHERE id = ${id}`) `` in Node services.

**Fix:** Parameterised queries: `db.query("SELECT … WHERE id = $1", [id])` (pg) or the
query builder/ORM equivalent.

## eval and Function constructor (OWASP A03: Injection)

**Pattern:** `eval(input)` or `new Function(input)` for "dynamic" behaviour.

**Fix:** `JSON.parse` for data; explicit dispatch maps for behaviour selection.

## Hallucinated or typosquatted dependencies (OWASP A06: Vulnerable and Outdated Components)

**Pattern:** AI assistants occasionally invent package names or suggest abandoned packages;
attackers register these names ("slopsquatting").

**Fix:** Verify every AI-suggested package on the npm registry (downloads, maintenance,
repository link) before installing. Prefer well-known packages already in the lockfile.

## Permissive CORS (OWASP A05: Security Misconfiguration)

**Pattern:** `app.use(cors({ origin: "*" }))` or `Access-Control-Allow-Origin: *` added to
"fix" a CORS error during development, with credentials enabled.

**Fix:** Allowlist exact origins from configuration; never combine wildcard origins with
credentials.
