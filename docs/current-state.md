# Current State of Fix My Vibe

_A plain-language status report — where the project actually is today, written so you can build a demo presentation from it._

_Last reviewed: 25 June 2026, against the `main` branch of the repo (latest commit merged the `mcp-server` branch)._

---

## The one-paragraph version

Fix My Vibe is a tool that looks at any code project, works out which AI coding assistants you're using (Claude Code, Cursor, Copilot, Windsurf, Aider), spots what's missing or set up badly, and then generates the right config files for you — only writing anything to disk after you say yes. It started life as a hackathon CLI tool, and it has since grown a second "front door": it now also runs as an **MCP server**, which means an AI agent like Claude Code or Copilot Chat can call it directly as a set of tools. The core idea — *scan → research → plan → confirm → write* — is fully built and runs end to end. The polish needed to make it a clean demo is mostly documentation and presentation, not missing engineering.

**Status: working tool, not yet a packaged demo.** The engine runs; the storefront needs tidying.

---

## What it does (the user story)

Imagine you've just opened a messy project in VS Code. You ask Claude Code "fix my vibe." Behind the scenes:

1. It **scans** your project — detects your stack (e.g. Python + FastAPI), which AI tools you use, and security smells in your code.
2. It **researches** best practices for what it found (from a curated knowledge base, with web search as backup).
3. It **plans** a ranked list of fixes — e.g. "you have no `CLAUDE.md`", "your `.env` isn't gitignored", "here's a `SECURITY.md` for the issues I found."
4. It **asks you** which fixes to apply — one checkbox per fix.
5. It **writes** only the files you ticked (backing up anything it overwrites) and then **verifies** they came out right.

That's the whole pitch, and it works today.

---

## What tech we're using

Grouped simply, from "definitely core" to "supporting":

**The core engine (Python)**
- **Python 3.11+** — the whole thing.
- **A five-agent pipeline**: Scanner → Researcher → Planner → Executor → Verifier. Each agent is a separate module with one job. An **Orchestrator** runs them in order and enforces that only one step is ever allowed to write files.

**The two ways to run it**
- **CLI** — `fix-my-vibe /path/to/project`. The original front door.
- **MCP server** (`src/mcp_server.py`) — the newer front door. Built on the official MCP Python SDK (FastMCP), talks over stdio, and exposes three tools an agent can call: `scan_project`, `propose_fixes`, `apply_fixes`. Both front doors share the *same* engine, so they behave identically.

**The "smart" cloud layer (optional — "Foundry mode")**
- **Azure AI Foundry** runs the agents as real LLM-powered reasoning agents.
- **Azure AI Search** is the grounding knowledge base: **32 curated sources** (OWASP, CWE, NIST, plus framework and AI-tool docs) that were fetched, chunked, embedded, and indexed by a real ingestion pipeline (`kb/ingest_security_kb.py`). This is what makes the config advice *grounded* rather than made-up.
- **Tavily** is a web-search fallback for anything current or novel the KB doesn't cover.

**The "no cloud needed" layer ("local mode")**
- If you don't set up Azure, it falls back to **pure-Python local mode** — no LLM, no internet, uses built-in static best-practice content. Less rich, but runs anywhere with zero setup. _(I confirmed this mode runs end to end.)_

**Infra / packaging**
- `azd` provisioning templates (`infra/main.bicep`, `azure.yaml`) for standing up the Azure resources.
- Installable as a package (`pip install -e .`) with two entry points: `fix-my-vibe` (CLI) and `fix-my-vibe-mcp` (server).

---

## What's actually working right now (verified)

I didn't just read the notes — I ran it. These are confirmed:

- **Local mode runs the full loop.** On a test project it correctly detected Python + FastAPI, found 7 code-level security issues, planned fixes, wrote `.gitignore` and `SECURITY.md`, and verified both. No files touched until confirmed.
- **The MCP server boots and registers all three tools** (`scan_project`, `propose_fixes`, `apply_fixes`).
- **The safety gate is genuinely safe.** The only code path that writes files is the "apply" phase, and it only runs after explicit confirmation. If the calling app *can't* ask you for confirmation, the tool writes nothing and tells you to review instead. This is a real fail-safe, not a comment in the code.
- **The code is honest.** Everything the progress notes claim is backed by actual code — no phantom features.

One thing I could **not** verify myself: **Foundry mode** (the impressive Azure AI Search + LLM-agent path), because I don't have your Azure credentials. Everything above about the live KB is based on reading the code and your own end-to-end test logs, which look solid — but for a demo you'll want to run it once yourself to be sure the Azure resources are still live.

---

## What's cool and unique

These are the things worth leading with in a presentation:

- **It lives where you already work.** Because it's an MCP server, you don't run a separate app — you just talk to Claude Code or Copilot and it does the work in place. That's a genuinely modern, "agentic" framing.
- **One engine, two front doors.** The CLI and the MCP server share the exact same orchestrator, cleanly split so that planning *can never* accidentally write files and applying is the only writer. That's a nice architecture story for an interview.
- **Confirmation built right into the protocol.** The MCP "apply" tool uses an elicitation prompt (a checkbox per fix) as its confirmation gate — a thoughtful, safety-first design that maps the original "never write without asking" rule cleanly onto how agents actually work.
- **Grounded, not guessed.** The config and security advice is backed by a real knowledge base of authoritative sources (OWASP/CWE/NIST), not just the model's memory. This is the part that makes it more than a templating script.
- **It catches AI-introduced bugs.** The security scanner looks specifically for patterns AI assistants commonly introduce (hardcoded secrets, `eval()`, disabled SSL verification, etc.) — a clever, on-theme angle for a tool aimed at "vibe coders."
- **It degrades gracefully.** Works fully offline in local mode; gets smarter when you plug in Azure. Good for demos on a flaky conference wifi.

---

## What's missing from the project that was planned

Things that were on the roadmap or in early notes but aren't finished — none are fatal, and most you can simply decide not to do:

- **The "visible reasoning" demo angle.** Early notes wanted the model's chain-of-thought shown live as a "thinking out loud" feature (it was a hackathon prize criterion). The reasoning trace *is* captured internally, but it's deliberately stripped from the MCP output and isn't surfaced as a feature. Since the hackathon is over, this is optional — but it could be a nice demo moment if you want it back.
- **The model story is a bit muddled.** The setup template still points at `Phi-4-reasoning` from the original plan, while the knowledge-base work moved on to Azure OpenAI embeddings and a smaller model. Worth picking one clear story.
- **Knowledge-base filtering is a workaround, not a finish.** Two KB fields (`threat_categories`, `stack_applicable_to`) weren't set up as filterable, so the code folds those hints into the search text instead of true filtering. Works fine; just not the "proper" version.
- **Convention detection is partial.** It reads project conventions well for Python but only detects `snake_case` naming — no JavaScript/TypeScript `camelCase` yet.
- **The old token-savings metric was never proven.** A claimed "340k → 140k token reduction" was correctly never used because it was never benchmarked. Leave it out unless you measure it.

---

## What's missing to make it demo-worthy

This is the actual to-do list to get from "works on my machine" to "clean demo presentation." In rough priority order:

1. **Rewrite the README.** This is the single most important fix. The current README still describes the *old* project — it says "Agents League hackathon," lists "Bing Grounding" (which was replaced by Azure AI Search + Tavily), describes only the CLI, and **doesn't mention the MCP server at all** — the headline feature. Anyone who looks at the repo gets the wrong idea immediately. A fresh README that leads with the MCP server and the "fix my vibe in Claude Code" story would transform first impressions.

2. **Record a short demo (or write a tight demo script).** There are currently **no demo assets** — no video, no GIF, no walkthrough. A 60–90 second clip of "open messy project → ask Claude Code to fix my vibe → checkboxes appear → files written" is what actually sells this. Decide whether you film local mode (safe, offline, but only generates a couple of files) or Foundry mode (impressive, full output, but needs live Azure and has rate limits — consider pre-recording it).

3. **Fix the setup template (`.env.example`).** It's missing the three `AZURE_SEARCH_*` variables the knowledge base actually needs, so anyone following it would get a broken Foundry mode. Fast fix, big payoff for reproducibility.

4. **Pick a clean demo project.** The current test fixture detects *no* AI tools, so local mode only produced 2 files in my run. For a demo you want a sample project where tools *are* detected, so the fuller output set (`CLAUDE.md`, `.cursorrules`, `.cursorignore`, `SECURITY.md`, `.gitignore`) all show up and the tool looks impressive. (Use `tests/fixtures/demo-shop` — it's purpose-built for this.)

5. **(Nice to have) A couple of real automated tests.** Right now there are only smoke scripts and fixtures, no proper test suite. Not essential for a personal project, but a small `pytest` file or two reads well to anyone reviewing the repo.

---

## Suggested framing for the presentation

If you want a spine for the demo narrative:

> "AI coding assistants only work as well as their setup files — and most people never write good ones. Fix My Vibe is an MCP server that plugs into Claude Code or Copilot. You ask it to fix your vibe, it scans your project, checks your setup against a grounded knowledge base of security and best-practice sources, and generates the right config files — only writing them after you tick which ones you want."

Lead with the MCP-server-in-your-editor angle, show the confirmation checkboxes (the safety story), and mention the grounded knowledge base as the reason the advice is trustworthy. Those three beats are your strongest, and they're all genuinely built.
