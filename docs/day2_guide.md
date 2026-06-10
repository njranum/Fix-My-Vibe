# Fix My Vibe — Day 2 Guide

*Microsoft AI Skills Fest, Agents League — Day 2: Wed 11 Jun 2026*  
*Goal: Full Foundry pipeline tested, Researcher (Tavily) working, Planner reasoning trace captured for demo*

---

## What Day 2 Delivers

By end of day:
- Full Foundry pipeline tested end-to-end (all 5 agents in Foundry mode)
- Tavily web search confirmed working inside Researcher agent
- Planner reasoning trace captured — this is the Best Reasoning Agent demo moment
- Monorepo fixture added for submission depth
- README.md drafted for public repo

---

## Going into Day 2 — What Works

| Component | Status | Mode |
|-----------|--------|------|
| Scanner — 3-layer detection | ✅ | Local + Foundry |
| Researcher — static KB | ✅ | Local |
| Researcher — Tavily enrichment | ✅ (coded, not tested live) | Foundry |
| Planner — ActionPlan generation | ✅ | Local + Foundry |
| Executor — confirmation gate + writes | ✅ | Local + Foundry |
| Verifier — section checks + quality | ✅ | Local + Foundry |
| Full local pipeline (all 3 fixtures) | ✅ | Local |
| Full Foundry pipeline | 🔲 untested | Foundry |

**Architecture reminder:**  
Phi-4-reasoning does NOT support function calling in the Agents API. All five agents use:
```
client.inference.get_chat_completions_client()
```
Tools (scan, write, verify, Tavily) run in Python — results are passed as context to Phi-4 for plain-text reasoning.

**Search provider:** Tavily (not Bing Grounding). Set `TAVILY_API_KEY` in `.env`.

---

## 2.1 — Pre-flight checks

Before running Foundry mode, confirm:

```bash
# Azure auth
az account show   # must show the right subscription

# .env must have all three
grep FOUNDRY_PROJECT_ENDPOINT .env
grep FOUNDRY_MODEL_DEPLOYMENT_NAME .env
grep TAVILY_API_KEY .env
```

Expected `.env` values:
```
FOUNDRY_PROJECT_ENDPOINT=https://<resource>.services.ai.azure.com/api/projects/fix-my-vibe
FOUNDRY_MODEL_DEPLOYMENT_NAME=Phi-4-reasoning
TAVILY_API_KEY=tvly-...
```

> **Case-sensitive:** The deployment name is `Phi-4-reasoning` — not `phi-4-reasoning`.  
> Check the exact name in Foundry portal → Models + Endpoints if in doubt.

---

## 2.2 — Test the full Foundry pipeline

Run the cursor-project fixture end-to-end in Foundry mode:

```bash
python3 src/cli.py tests/fixtures/cursor-project --yes
```

Without `--local`, the CLI routes through Foundry when `FOUNDRY_PROJECT_ENDPOINT` is set.  
`--yes` auto-confirms all actions.

Expected: 3 files written and verified (`.gitignore` update, `.cursorignore`, `CLAUDE.md`).

If Foundry is unreachable, the orchestrator falls back to local mode automatically — check the output header for `Fix My Vibe (Foundry mode)` to confirm it's using Foundry.

---

## 2.3 — Capture the Planner reasoning trace (demo shot)

Run with `--verbose` to see Phi-4's step-by-step security analysis:

```bash
python3 src/cli.py tests/fixtures/cursor-project --yes --verbose
```

The Planner reasoning trace will appear between `── Planner Reasoning ──` bars. It contains Phi-4's chain-of-thought:
1. Are security issues correctly ranked first?
2. Is each missing config fix correct?
3. Any risk of overwriting intentional files?
4. What should the developer do first?

Ends with: `VERDICT: approved` or `VERDICT: needs_revision`

**This trace is the Best Reasoning Agent prize submission moment — screenshot or record it.**

Also run the node-typescript fixture to capture a TypeScript/Copilot reasoning trace:

```bash
python3 src/cli.py tests/fixtures/node-typescript --yes --verbose
```

---

## 2.4 — Verify Tavily in Researcher output

Run `--scan-only` first to confirm Foundry connectivity, then run with `--json` to inspect the Researcher output in the full pipeline:

```bash
python3 src/cli.py tests/fixtures/cursor-project --yes --json 2>&1 | python3 -c "
import sys, json
data = json.load(sys.stdin)
r = data.get('research_result', {})
print('Mode:', r.get('mode'))
print('Search summary:', r.get('search_summary'))
research = r.get('research', {})
for tool, info in research.items():
    urls = info.get('source_urls', [])
    if urls:
        print(f'{tool} URLs: {urls}')
"
```

If Tavily is working you'll see `mode: foundry` and URLs attached to each detected tool.  
If Tavily key is missing or the search fails, you'll see `Tavily unavailable — static KB used.` — the pipeline still completes, just with static best-practice knowledge.

---

## 2.5 — Test all three fixtures in Foundry mode

Reset fixtures first (remove files written in previous runs):

```bash
# bare-project — remove generated files, restore fixture to clean state
rm -f tests/fixtures/bare-project/CLAUDE.md
rm -f tests/fixtures/bare-project/.gitignore

# cursor-project — restore original .gitignore (no env section), remove CLAUDE.md/.cursorignore
git checkout -- tests/fixtures/cursor-project/.gitignore
rm -f tests/fixtures/cursor-project/CLAUDE.md
rm -f tests/fixtures/cursor-project/.cursorignore

# node-typescript — restore original .gitignore, remove generated files
git checkout -- tests/fixtures/node-typescript/.gitignore
rm -f tests/fixtures/node-typescript/CLAUDE.md
rm -f "tests/fixtures/node-typescript/.github/copilot-instructions.md"
```

Then run each:

```bash
python3 src/cli.py tests/fixtures/bare-project --yes
python3 src/cli.py tests/fixtures/cursor-project --yes
python3 src/cli.py tests/fixtures/node-typescript --yes
```

Expected outcomes:
- `bare-project`: 2 files written — `.gitignore` (create), `CLAUDE.md` (create)
- `cursor-project`: 3 files written — `.gitignore` (update), `.cursorignore` (create), `CLAUDE.md` (create)
- `node-typescript`: 3 files written — `.gitignore` (update), `CLAUDE.md` (create), `.github/copilot-instructions.md` (create)

---

## 2.6 — Add monorepo fixture

Create `tests/fixtures/monorepo/` to demonstrate multi-package detection:

```
tests/fixtures/monorepo/
├── package.json          ← pnpm workspace root
├── pnpm-workspace.yaml   ← workspace definition
├── .gitignore            ← node_modules/ only — no .env.*
├── README.md
├── apps/
│   └── web/
│       ├── package.json  ← Next.js app
│       └── .env.local    ← NEXT_PUBLIC_API_URL exposed
└── packages/
    └── shared/
        └── package.json  ← shared utilities
```

Key detection points:
- `pnpm-workspace.yaml` → stack: `node`, `nextjs`
- `.env.local` in `apps/web/` not covered by root `.gitignore` → `exposed_env` security issue
- No `.github/copilot-instructions.md` → `copilot` missing config (if Copilot in PATH)
- No `CLAUDE.md` → `claude_code` missing config

Expected output from scanner:
```
Stack: node, nextjs
Security issues: 1 (apps/web/.env.local not in .gitignore)
Missing configs: claude_code (CLAUDE.md), copilot (.github/copilot-instructions.md)
```

---

## 2.7 — Write README.md

The public README needs to land in the repo root before submission. Required sections:

1. **What it does** — one paragraph, no jargon
2. **Demo** — link to video or GIF
3. **Quickstart** — `pip install`, `fix-my-vibe <path>`, expected output
4. **Architecture** — the 5-agent diagram from CLAUDE.md
5. **Why reasoning matters** — explain why Phi-4's step-by-step analysis produces better config files than templates
6. **Azure setup** — FOUNDRY_PROJECT_ENDPOINT, az login, Tavily key
7. **Fixtures** — what each of the 3+ fixture projects demonstrates

---

## 2.8 — End of Day 2 checklist

- [ ] `python3 src/cli.py tests/fixtures/cursor-project --yes` succeeds in Foundry mode (no `--local`)
- [ ] Output header shows `Fix My Vibe (Foundry mode)` — confirms Foundry routing
- [ ] `--verbose` shows Planner reasoning trace with VERDICT line
- [ ] `--json` output contains `research_result.mode: "foundry"` 
- [ ] Tavily URLs appear in at least one tool's `source_urls` (or graceful fallback logged)
- [ ] All three fixtures verified in Foundry mode: bare, cursor, node-typescript
- [ ] `tests/fixtures/monorepo/` created and passes scanner in local mode
- [ ] README.md drafted in repo root

```bash
git add src/ tests/ docs/ README.md
git commit -m "feat: full Foundry pipeline working, Tavily researcher, monorepo fixture"
git push
```

---

## Architecture reference (current)

```
User → CLI (src/cli.py)
         └─→ Orchestrator (src/orchestrator.py)
               ├─→ Scanner     run_with_foundry(client, path)
               │     Tools:    scan_directory, check_path_tools, check_vscode_extensions,
               │               read_existing_context_file, infer_project_conventions
               │     Phi-4:    priority assessment → _reasoning_trace
               │
               ├─→ Researcher  run_with_foundry(client, scan_result)
               │     Tools:    Tavily search (Python direct, no Agents API)
               │     Phi-4:    synthesis summary → _reasoning_trace
               │
               ├─→ Planner     run_with_foundry(client, scan_result, research)
               │     Tools:    ActionPlan built in Python
               │     Phi-4:    step-by-step security review + VERDICT → _reasoning_trace ★
               │
               ├─→ Executor    run_with_foundry(client, path, plan, confirmed_ranks)
               │     Tools:    write_file (Python direct, never via Agents API)
               │     Phi-4:    narrates what was written → _reasoning_trace
               │
               └─→ Verifier    run_with_foundry(client, path, exec_result, plan)
                     Tools:    verify_file, read_existing_context_file (Python direct)
                     Phi-4:    quality assessment + QUALITY rating → _reasoning_trace
```

★ = Best Reasoning Agent demo moment

---

## Reasoning traces — what to show in the demo

| Agent | Trace content | Classification |
|-------|--------------|----------------|
| Scanner | 3-5 sentence priority assessment | `PRIORITY: high/medium/low` |
| Researcher | 2-3 sentence synthesis of Tavily results | `CONFIDENCE: high/medium/low` |
| Planner | 4-6 sentence step-by-step security review | `VERDICT: approved/needs_revision` |
| Executor | 2-3 sentence developer log of what was written | (no classification) |
| Verifier | 3-5 sentence qualitative quality assessment | `QUALITY: excellent/good/acceptable/needs_work` |

---

## Troubleshooting

**`server_error` from Phi-4:** Old code path using Agents API. All five agents must use `client.inference.get_chat_completions_client()`. Never use `client.agents.*` with Phi-4-reasoning.

**Researcher shows `Tavily unavailable`:** Check `TAVILY_API_KEY` in `.env` and that `python-dotenv` loaded it (`load_dotenv()` is called in `cli.py`). The pipeline completes with static KB — this is the designed fallback.

**`DefaultAzureCredential` fails:** Run `az login`. Corporate tenant: `az login --tenant <tenant-id>`.

**Files written in Foundry mode look identical to local mode:** This is correct — file content comes from local Python generators (`_generate_claude_md`, etc.). Phi-4 provides reasoning traces, not file content. The content is always deterministic and stack-aware.

**Fixture `.gitignore` already has `.env`:** If you ran the pipeline previously and didn't reset, the gitignore update action will be skipped (correctly — idempotent). Reset using `git checkout -- tests/fixtures/<name>/.gitignore` before re-testing.

---

*Day 2 of 4 — fix-my-vibe*  
*Next: Day 3 — Demo video, README polish, Foundry tracing visible in portal, submission prep*
