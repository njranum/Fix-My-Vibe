# Fix My Vibe — Day 3 Guide

*Microsoft AI Skills Fest, Agents League — Day 3: Thu 12 Jun 2026*  
*Goal: Rewrite all 5 agents to be genuinely agentic using o4-mini via the Foundry Agents API*

---

## The Problem Being Fixed Today

The Day 2 pipeline was not genuinely agentic. Phi-4-reasoning does not support function
calling via the Agents API, so the workaround was: Python runs all tools, collects results,
hands them to Phi-4, which writes a summary paragraph. The model was a narrator, not a
decision-maker.

**o4-mini fixes this.** It supports function calling via the Agents API. The model receives
tool definitions, decides what to call, and reasons over results as they come in.

---

## The Pattern (same for all 5 agents)

Every `run_with_foundry()` method now follows this structure:

```python
def run_with_foundry(client, ...) -> dict:
    # 1. Create agent with tool definitions
    agent = client.agents.create_agent(
        model=os.environ["FOUNDRY_MODEL_DEPLOYMENT_NAME"],  # "o4-mini"
        name="agent-name",
        instructions=AGENT_INSTRUCTIONS,
        tools=_get_tool_definitions(),
    )

    # 2. Send the task
    thread_id = create_thread_and_send(client, task_message)

    # 3. Run — model decides which tools to call, Python executes them
    run_agent_with_tools(client, agent.id, thread_id, _make_tool_handlers(...))

    # 4. Extract result + reasoning trace
    raw, reasoning = get_last_assistant_message_with_reasoning(client, thread_id)
    result = parse_json_response(raw)
    result["_reasoning_trace"] = reasoning

    # 5. Cleanup
    client.agents.delete_agent(agent.id)
    return result
```

`run_agent_with_tools()` in `foundry_utils.py` handles the polling loop:
- Polls run status
- When status = `requires_action`: executes tool calls locally, submits outputs back
- When status = `completed`: exits loop

---

## Agent-by-Agent Plan

### Scanner
- Tools: scan_directory, check_path_tools, check_vscode_extensions,
  read_existing_context_file, infer_project_conventions
- Task message: `"Scan the project at {path}. Return a ScanResult JSON."`
- What changes: model calls tools in whatever order makes sense, may skip tools
  if not relevant (e.g. no .vscode/ → skip extension check)

### Researcher
- Tools: search_web (Tavily)
- Task message: scan result JSON + "Research best practices for each detected tool"
- What changes: model chooses its own queries, may run more searches for tools
  with security issues

### Planner
- Tools: none (pure reasoning)
- Task message: scan result + research + "Generate an ActionPlan JSON with actual
  file content for each action"
- What changes: model generates real CLAUDE.md / .cursorrules content, not Python
  templates. This is the most important change for output quality.
- Output must include `content` field with complete file content for each action

### Executor
- Tools: write_file
- Task message: confirmed actions only (after user approval gate)
- Confirmation gate stays in Python — user approves before agent is created
- What changes: model calls write_file for each confirmed action

### Verifier
- Tools: verify_file, read_existing_context_file
- Task message: list of written files + expected sections
- What changes: model decides what to check, makes qualitative judgments

---

## Planner Instructions (critical)

The Planner instructions must be explicit that the model generates real content:

```
You are the Planner agent for Fix My Vibe. You reason carefully about what a developer
needs to fix in their AI coding tool setup and produce a prioritised, actionable plan.

CRITICAL: For each action, you must write the COMPLETE file content. Do not use
placeholders. Do not write templates. Write real content specific to this project based
on the scan result and research provided.

For CLAUDE.md:
- Use the readme_summary from conventions as the Overview
- Include the exact test_command, build_command, lint_command found in conventions
- Add DO NOT rules specific to the detected stack
- Reference actual directories found in key_directories

For .cursorrules:
- Reference the actual stack versions and frameworks
- Include the exact linting/testing tools detected

Return ActionPlan JSON where every action's "content" field contains the complete,
ready-to-write file content.
```

---

## Testing Plan

After each agent rewrite, test it in isolation before wiring into the pipeline:

```bash
# Test scanner alone
.venv/bin/python3 -c "
from src.foundry_utils import get_client
from src.agents.scanner import run_with_foundry
from dotenv import load_dotenv; load_dotenv()
client = get_client()
result = run_with_foundry(client, 'tests/fixtures/cursor-project')
print(result.get('detected_tools'))
print(result.get('_reasoning_trace', '')[:200])
"

# Test full pipeline on cursor-project
.venv/bin/python3 src/cli.py tests/fixtures/cursor-project --yes --verbose
```

Expected after full rewrite:
- `--verbose` shows tool call decisions in reasoning trace ("I see .cursorrules, calling read_existing_context_file...")
- Planner-generated CLAUDE.md references FastAPI, the actual README summary
- Different fixtures produce different CLAUDE.md content

---

## 3.1 — End of Day 3 Checklist

- [ ] Scanner `run_with_foundry()` uses Agents API, model calls tools
- [ ] Researcher `run_with_foundry()` uses Agents API, model generates queries
- [ ] Planner `run_with_foundry()` generates actual file content via o4-mini
- [ ] Executor `run_with_foundry()` uses Agents API, model calls write_file
- [ ] Verifier `run_with_foundry()` uses Agents API, model calls verify tools
- [ ] Full pipeline on cursor-project: 3 files written, reasoning traces show tool decisions
- [ ] Planner output for bare-project ≠ Planner output for node-typescript
- [ ] All 3 fixtures pass end-to-end in Foundry mode

```bash
git add src/ docs/
git commit -m "feat: genuinely agentic pipeline with o4-mini and Agents API"
git push
```

---

## What Good Looks Like

**Scanner reasoning trace (target):**
```
I can see requirements.txt — checking its content for stack signals...
scan_directory shows fastapi in requirements.txt, detected: python, fastapi.
There's a .cursorrules file — calling read_existing_context_file to audit quality...
The .cursorrules is only 3 lines, no prohibition section. Flagging as quality issue.
No .cursorignore present — this is a security issue since .env exists.
Calling infer_project_conventions to check for test/build commands...
```

**Planner output (target):**
```
# cursor-project

## Overview
A FastAPI REST API for managing user data, built with SQLAlchemy and PostgreSQL.

## Stack
- python
- fastapi
- sqlalchemy

## Commands
- **Test:** `pytest`
- **Lint:** `ruff check .`

## DO NOT
- Do not commit `.env` files or secrets
- Do not use bare `except:` — always catch specific exceptions
- Do not expose raw database errors to API responses
- Do not use `print()` for logging — use the logging module
```

Note that "A FastAPI REST API for managing user data, built with SQLAlchemy and PostgreSQL"
comes from the project's actual README — the model read it and used it. A template cannot do this.

---

*Day 3 of 4 — fix-my-vibe*  
*Next: Day 4 — README, demo video, submission*
