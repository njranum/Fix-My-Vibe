---
description: Run Fix My Vibe on a project via the Foundry MCP tools
argument-hint: "[project path — optional]"
---
Run **Fix My Vibe** on a project: a team of Azure AI Foundry agents that diagnoses a
project's AI-coding setup, plans fixes grounded in an Azure AI Search knowledge base
(**Foundry IQ**), and applies them with confirmation. They're exposed to you as the
`fix-my-vibe` MCP tools: `scan_project`, `propose_fixes`, `apply_fixes`.

Setup — do this SILENTLY (run the commands, don't narrate them): get the repo root with
`pwd`. If a target path was given in `$ARGUMENTS`, use it as-is. Otherwise prepare a
fresh writable copy of the project and use THAT as the target:
`rm -rf .fmv-run/shop-api && mkdir -p .fmv-run && cp -R tests/fixtures/demo-shop .fmv-run/shop-api`
The target's absolute path is then `<repo-root>/.fmv-run/shop-api`.

TOOL CALLS: every `fix-my-vibe` tool takes exactly ONE argument, named `project_path`,
set to that absolute path. Do not guess or add other argument names. Call them exactly as:
`scan_project(project_path="<repo-root>/.fmv-run/shop-api")`,
`propose_fixes(project_path="<repo-root>/.fmv-run/shop-api")`,
`apply_fixes(project_path="<repo-root>/.fmv-run/shop-api")` — the SAME path in all three.

Narrate for an audience — short, clear sentences, no walls of JSON. Do NOT call this a
"demo", and do not use the words demo / sample / fixture / test in what you say. Present
it as a real run on a real project. You may describe the project itself briefly: a small
Flask shop API, quickly vibe-coded, with no AI config, secrets in a committed `.env`, and
a couple of security bugs.

1. **Diagnose** — `scan_project(project_path=...)`. Confirm `mode` is `foundry`, then
   summarise the 5 problems: missing CLAUDE.md, exposed `.env`, a hardcoded secret, a
   SQL injection, and Flask debug mode left on.

2. **Plan** — `propose_fixes(project_path=...)`. Walk through the ranked plan: the config
   files it will create and the code remediations. Call out the OWASP / KB citations on
   the fixes — that is Foundry IQ grounding them in real sources, not the model's memory.

3. **Apply** — `apply_fixes(project_path=...)`. It shows a confirmation prompt with one
   checkbox per fix; tick the ones to apply. Selected files are written with backups,
   then verified. Report what was written/fixed and the verification summary.

4. **Wrap up** — one or two sentences: what got fixed, and that every change was
   confirmed before writing and verified afterwards.
