# AI Tool Configuration Hygiene

How AI coding tool configuration itself becomes a security surface. Part of the
Fix My Vibe knowledge base.

## .env files readable by AI tools

**Pattern:** Cursor, Claude Code, and similar tools index or read project files for
context. A `.env` in the project root flows into the AI context window — and from there
potentially into completions, logs, or telemetry.

**Fix:** `.cursorignore` (Cursor) must list `.env`, `.env.*`, `*.pem`, `*.key`,
`credentials.json`, `secrets/`. Claude Code: add a deny rule for secret paths or keep
secrets outside the workspace. And `.gitignore` them regardless.

## Secrets pasted into context files

**Pattern:** Connection strings or tokens written into CLAUDE.md / .cursorrules "so the
AI knows the environment". Context files are committed and shared.

**Fix:** Context files describe *where* configuration comes from ("DB URL via
DATABASE_URL env var"), never the values.

## Missing DO NOT rules

**Pattern:** Context files that only describe the stack. The AI then repeats the same
mistakes every session — bare excepts, `any` types, committed debug flags.

**Fix:** Every CLAUDE.md / .cursorrules needs an explicit DO NOT section encoding the
project's known failure modes (including the security patterns in this knowledge base),
so the assistant is told once, permanently.

## Overly broad tool permissions

**Pattern:** Allow-all permission configs (auto-approve every shell command or file
write) set up to reduce friction.

**Fix:** Allowlist specific safe commands; keep file writes gated on review for paths
outside the working tree; never auto-approve commands that can exfiltrate (curl, scp)
or destroy (rm -rf) without inspection.

## Stale context files

**Pattern:** CLAUDE.md describing commands and structure that no longer exist; the AI
acts on wrong information with confidence.

**Fix:** Treat context files like code — update them in the same PR that changes the
build/test workflow. Re-run a setup audit (e.g. fix-my-vibe) after significant changes.
