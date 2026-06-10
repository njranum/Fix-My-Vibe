# Fix My Vibe — Day 0 & Day 1 Build Plan

*Microsoft AI Skills Fest, Agents League — Deadline June 14, 2026*
*This document: tonight (Day 0) + tomorrow (Day 1, Tue 10 Jun)*

---

## Model strategy (updated)

**All agents use `Phi-4-reasoning`** — one model, no exceptions.
- No quota issues (Microsoft's own model, serverless API, broadly available in all regions)
- Optimised for chain-of-thought reasoning — directly supports the "Best Reasoning Agent" prize
- Emits `<think>...</think>` blocks before its JSON answers — these are surfaced as visible
  reasoning traces in `--verbose` mode; see the Reasoning Traces section below

**Web search: Tavily** (replaces Bing Grounding, which requires a paid Azure tier).
- Tavily is purpose-built for AI agents, free tier covers hackathon use
- Implemented as a custom function tool in the Researcher agent — same pattern as Scanner
- Set `TAVILY_API_KEY` in `.env` (get key at tavily.com)

---

## Day 0 — Tonight: Azure Provisioning & Repo

Estimated time: ~2 hours. Nothing from Day 1 works until this is done.

### 0.1 — Check prerequisites (15 min)

```bash
# Confirm everything installed
python --version        # Need 3.9+
az --version            # Azure CLI
az login                # Authenticate — confirm correct subscription appears
az account show         # Check subscription ID and name
```

If `az` not installed: https://docs.microsoft.com/cli/azure/install-azure-cli

```bash
# Install Python SDK packages now — you'll need them tomorrow
pip install azure-ai-projects azure-identity python-dotenv
```

### 0.2 — Create the Azure AI Foundry project (30 min)

Do this in the Azure portal UI — faster and less error-prone than CLI for first-time setup.

1. Go to https://ai.azure.com
2. Click **Create project**
3. Settings:
   - Name: `fix-my-vibe`
   - Region: **East US** or **West Europe** (best model availability)
   - Resource group: create new `fix-my-vibe-rg`
4. Click through — it creates an Azure AI Services account and a Foundry project
5. Once created, go to the project homepage
6. Copy your **project endpoint URL** — it looks like:
   `https://<your-resource-name>.services.ai.azure.com/api/projects/fix-my-vibe`
7. Save this as `FOUNDRY_PROJECT_ENDPOINT` — you'll need it constantly

### 0.3 — Deploy models (10 min)

From inside the Foundry project in the portal:

1. Go to **Models + Endpoints** → **Deploy model**
2. Search for **`Phi-4-reasoning`** → select it → **Deploy**
   - Deployment type: **Serverless API** (no quota reservation, pay-per-token)
   - Name the deployment: `Phi-4-reasoning`
   - Note the name exactly — it's case-sensitive in SDK calls

That's it. All five agents use this one deployment.

### 0.4 — Set up Tavily web search (5 min)

Tavily replaces Bing Grounding (Bing requires a paid Azure tier). Tavily is purpose-built
for AI agent web search and has a free tier sufficient for the hackathon.

1. Go to **tavily.com** → sign up → copy your API key
2. Add it to your `.env` as `TAVILY_API_KEY=tvly-...`
3. Install: `pip install tavily-python`

The Researcher agent calls Tavily as a custom function tool — no Azure portal config needed.

### 0.5 — Set up the GitHub repo (20 min)

```bash
# Create locally
mkdir fix-my-vibe && cd fix-my-vibe
git init

# Create the project structure
mkdir -p agents tools tests fixtures/bare-project

touch agents/__init__.py
touch agents/scanner.py
touch agents/researcher.py
touch agents/planner.py
touch agents/executor.py
touch agents/verifier.py
touch agents/orchestrator.py
touch tools/__init__.py
touch tools/fs_tools.py
touch cli.py
touch requirements.txt
touch .env.example
touch .gitignore
touch README.md
```

**`.gitignore`** — add immediately:
```
.env
__pycache__/
*.pyc
.venv/
dist/
*.egg-info/
.DS_Store
```

**`.env.example`** — template for others (commit this, not `.env`):
```
FOUNDRY_PROJECT_ENDPOINT=https://<your-resource>.services.ai.azure.com/api/projects/<project>
FOUNDRY_MODEL_DEPLOYMENT_NAME=Phi-4-reasoning
FOUNDRY_MODEL_MINI_DEPLOYMENT_NAME=Phi-4-reasoning
TAVILY_API_KEY=tvly-your-key-here
```

**`.env`** — your real values (never commit):
```
FOUNDRY_PROJECT_ENDPOINT=<paste your endpoint here>
FOUNDRY_MODEL_DEPLOYMENT_NAME=Phi-4-reasoning
FOUNDRY_MODEL_MINI_DEPLOYMENT_NAME=Phi-4-reasoning
TAVILY_API_KEY=tvly-your-key-here
```

**`requirements.txt`**:
```
azure-ai-projects>=1.0.0
azure-identity>=1.15.0
python-dotenv>=1.0.0
```

### 0.6 — Smoke test the Foundry connection (20 min)

Create `test_connection.py` — run this to confirm everything works before Day 1:

```python
import os
from dotenv import load_dotenv
from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential

load_dotenv()

client = AIProjectClient(
    endpoint=os.environ["FOUNDRY_PROJECT_ENDPOINT"],
    credential=DefaultAzureCredential(),
)

# List deployments — confirms project is reachable
print("Connected. Deployments available:")
for deployment in client.deployments.list():
    print(f"  - {deployment.name}")

# Create a minimal test agent — confirms agent creation works
agent = client.agents.create_agent(
    model=os.environ["FOUNDRY_MODEL_DEPLOYMENT_NAME"],
    name="smoke-test",
    instructions="You are a test agent. Reply with OK.",
)
print(f"\nAgent created: {agent.id}")

# Run it
thread = client.agents.threads.create()
client.agents.messages.create(thread_id=thread.id, role="user", content="Say OK")
run = client.agents.runs.create_and_process(thread_id=thread.id, agent_id=agent.id)
print(f"Run status: {run.status}")

messages = client.agents.messages.list(thread_id=thread.id)
for msg in messages:
    if msg.role == "assistant":
        print(f"Response: {msg.content[0].text.value}")

# Clean up
client.agents.delete_agent(agent.id)
print("\nSmoke test passed. Ready for Day 1.")
```

```bash
python test_connection.py
```

If this prints "Smoke test passed" → Day 0 complete. Commit everything and push to GitHub.

### 0.7 — Push to GitHub and register (10 min)

```bash
git add .
git commit -m "chore: initial project structure and smoke test"
```

Create a new **public** repo on GitHub named `fix-my-vibe`, then:

```bash
git remote add origin https://github.com/<your-username>/fix-my-vibe.git
git branch -M main
git push -u origin main
```

Then: register at https://aka.ms/AgentsLeague/AISF if not done.

---

## Day 1 — Scanner Agent + CLI skeleton

Estimated time: ~5–6 hours. Goal: `python cli.py scan ./some-project` produces a
structured diagnosis to the terminal.

### 1.1 — File system tools (`tools/fs_tools.py`) (60 min)

These are the raw tools the Scanner agent calls. Pure Python, no Foundry yet.

```python
"""
tools/fs_tools.py
Raw filesystem tools for the Scanner agent.
These are called as function tools by the Foundry agent.
"""

import os
import shutil
import json
from pathlib import Path


# ── Config file signatures ─────────────────────────────────────────────────
# Maps AI tool name → list of config filenames that indicate it's in use
TOOL_CONFIG_SIGNATURES = {
    "claude_code": ["CLAUDE.md", ".claude/settings.json", "claude_desktop_config.json"],
    "cursor":      [".cursorrules", ".cursor/rules", ".cursorignore", ".cursorindexingignore"],
    "copilot":     [".github/copilot-instructions.md", ".copilot", ".copilotignore"],
    "windsurf":    [".windsurfrc", ".windsurf/rules.md"],
    "aider":       [".aider.conf.yml", ".aiderignore", "aider.conf.yml"],
    "cline":       [".clinerules", ".cline/settings.json"],
    "continue":    [".continue/config.json", ".continuerc.json"],
}

# Maps stack/framework → detection files
STACK_SIGNATURES = {
    "python":     ["requirements.txt", "pyproject.toml", "setup.py", "Pipfile", "poetry.lock"],
    "node":       ["package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml"],
    "nextjs":     ["next.config.js", "next.config.ts", "next.config.mjs"],
    "fastapi":    ["main.py"],  # combined with python
    "django":     ["manage.py", "settings.py"],
    "react":      ["src/App.jsx", "src/App.tsx", "vite.config.ts"],
    "typescript": ["tsconfig.json"],
    "rust":       ["Cargo.toml", "Cargo.lock"],
    "go":         ["go.mod", "go.sum"],
    "docker":     ["Dockerfile", "docker-compose.yml", "docker-compose.yaml"],
    "monorepo":   ["pnpm-workspace.yaml", "lerna.json", "nx.json", "turbo.json"],
}

# Security-sensitive file patterns
SENSITIVE_PATTERNS = [".env", ".env.local", ".env.production", "*.pem", "*.key", "secrets/"]

# Linter config files per stack
LINTER_SIGNATURES = {
    "python": ["ruff.toml", ".ruff.toml", "pyproject.toml", ".flake8", ".pylintrc"],
    "node":   [".eslintrc", ".eslintrc.js", ".eslintrc.json", ".eslintrc.yml",
               "eslint.config.js", "biome.json"],
    "all":    [".prettierrc", ".prettierrc.json", "prettier.config.js",
               ".editorconfig", "pre-commit-config.yaml", ".pre-commit-config.yaml"],
}


def scan_directory(project_path: str) -> dict:
    """
    Layer 1 + security scan: walk the directory, detect tools and stack
    from config file signatures, flag security issues.
    Returns a structured scan result dict.
    """
    path = Path(project_path).resolve()
    if not path.exists():
        return {"error": f"Path does not exist: {project_path}"}
    if not path.is_dir():
        return {"error": f"Path is not a directory: {project_path}"}

    # Collect all files (top 3 levels to avoid huge trees)
    all_files = set()
    for root, dirs, files in os.walk(path):
        # Skip hidden dirs and common noise
        dirs[:] = [d for d in dirs if not d.startswith('.') and
                   d not in ('node_modules', '__pycache__', '.git', 'dist', 'build', '.next')]
        rel_root = Path(root).relative_to(path)
        depth = len(rel_root.parts)
        if depth > 3:
            dirs.clear()
            continue
        for f in files:
            all_files.add(str(Path(root, f).relative_to(path)))

    # Detect AI tools (Layer 1: config signatures)
    detected_tools = []
    tool_files_found = {}
    for tool, signatures in TOOL_CONFIG_SIGNATURES.items():
        found = [s for s in signatures if s in all_files or Path(path, s).exists()]
        if found:
            detected_tools.append(tool)
            tool_files_found[tool] = found

    # Detect stack
    detected_stack = []
    for stack, signatures in STACK_SIGNATURES.items():
        if any(s in all_files or Path(path, s).exists() for s in signatures):
            detected_stack.append(stack)

    # Detect linters
    detected_linters = []
    for stack, sigs in LINTER_SIGNATURES.items():
        found = [s for s in sigs if s in all_files or Path(path, s).exists()]
        if found:
            detected_linters.extend(found)

    # Security scan
    security_issues = []
    has_gitignore = ".gitignore" in all_files
    gitignore_content = ""
    if has_gitignore:
        try:
            gitignore_content = Path(path, ".gitignore").read_text()
        except Exception:
            pass

    # Check for exposed .env files
    env_files = [f for f in all_files if f.startswith(".env") or "/.env" in f]
    for env_file in env_files:
        if env_file not in gitignore_content:
            security_issues.append({
                "type": "exposed_env",
                "file": env_file,
                "severity": "high",
                "description": f"{env_file} present but not in .gitignore — secrets may leak to AI context window"
            })

    # Check for .cursorignore if Cursor detected
    if "cursor" in detected_tools:
        if ".cursorignore" not in all_files:
            security_issues.append({
                "type": "missing_cursorignore",
                "severity": "high",
                "description": ".cursorrules detected but no .cursorignore — Cursor reads .env files by default"
            })

    # Determine missing context files (Layer 1 output)
    missing_configs = {}
    for tool in detected_tools:
        primary_config = TOOL_CONFIG_SIGNATURES[tool][0]  # First sig is the primary file
        if primary_config not in all_files and not Path(path, primary_config).exists():
            missing_configs[tool] = primary_config

    # Detect monorepo
    is_monorepo = "monorepo" in detected_stack
    subdirs = [d for d in path.iterdir() if d.is_dir() and not d.name.startswith('.')]

    return {
        "project_path": str(path),
        "detected_tools": detected_tools,
        "tool_files_found": tool_files_found,
        "detected_stack": [s for s in detected_stack if s != "monorepo"],
        "is_monorepo": is_monorepo,
        "detected_linters": detected_linters,
        "missing_configs": missing_configs,
        "security_issues": security_issues,
        "has_gitignore": has_gitignore,
        "total_files_scanned": len(all_files),
        "subdirectory_count": len(subdirs),
    }


def check_path_tools() -> dict:
    """
    Layer 2: Check which AI coding tools are on PATH.
    Supplements Layer 1 for tools that might not have config files yet.
    """
    path_tools = {}
    cli_names = {
        "claude_code": ["claude"],
        "cursor":      ["cursor"],
        "aider":       ["aider"],
        "copilot":     ["gh"],  # GitHub CLI hosts Copilot
        "windsurf":    ["windsurf"],
    }
    for tool, cmds in cli_names.items():
        for cmd in cmds:
            if shutil.which(cmd):
                path_tools[tool] = cmd
                break
    return path_tools


def read_existing_context_file(project_path: str, filename: str) -> dict:
    """
    Read an existing context file for audit.
    Returns content + basic quality metrics.
    """
    filepath = Path(project_path) / filename
    if not filepath.exists():
        return {"exists": False}

    try:
        content = filepath.read_text(encoding="utf-8")
        lines = content.splitlines()
        word_count = len(content.split())
        # Rough token estimate (1 token ≈ 4 chars)
        estimated_tokens = len(content) // 4
        has_overview = any(kw in content.lower() for kw in ["overview", "project", "about"])
        has_commands = any(kw in content.lower() for kw in ["npm", "pip", "make", "run", "test", "build"])
        has_do_not = "do not" in content.lower() or "don't" in content.lower()
        return {
            "exists": True,
            "filename": filename,
            "line_count": len(lines),
            "word_count": word_count,
            "estimated_tokens": estimated_tokens,
            "has_project_overview": has_overview,
            "has_commands_section": has_commands,
            "has_prohibitions": has_do_not,
            "quality_concerns": _audit_context_file(content, estimated_tokens),
            "content_preview": content[:500],
        }
    except Exception as e:
        return {"exists": True, "error": str(e)}


def _audit_context_file(content: str, token_count: int) -> list:
    concerns = []
    if token_count > 2000:
        concerns.append(f"Very long ({token_count} tokens) — consider splitting into layered files")
    if token_count < 100:
        concerns.append("Very short — likely missing key sections")
    if "todo" in content.lower() or "fixme" in content.lower():
        concerns.append("Contains TODO/FIXME markers — incomplete sections")
    if content.count("DO NOT") + content.count("don't") + content.count("never") < 1:
        concerns.append("No explicit prohibition section — add 'DO NOT' rules for common mistakes")
    if not any(kw in content.lower() for kw in ["test", "build", "run", "install"]):
        concerns.append("Missing commands section — agent won't know how to run tests or build")
    return concerns


def write_file(project_path: str, relative_path: str, content: str) -> dict:
    """
    Safe file write with backup. Called by Executor only after user confirmation.
    """
    target = Path(project_path) / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)

    backed_up = False
    if target.exists():
        backup_path = target.with_suffix(target.suffix + ".bak")
        target.rename(backup_path)
        backed_up = True

    target.write_text(content, encoding="utf-8")
    return {
        "written": str(target),
        "backed_up": backed_up,
        "size_bytes": len(content.encode("utf-8")),
    }


def verify_file(project_path: str, relative_path: str, expected_sections: list) -> dict:
    """
    Verify a written file contains expected sections. Called by Verifier agent.
    """
    target = Path(project_path) / relative_path
    if not target.exists():
        return {"verified": False, "reason": "File does not exist"}
    content = target.read_text(encoding="utf-8")
    missing = [s for s in expected_sections if s.lower() not in content.lower()]
    return {
        "verified": len(missing) == 0,
        "file": str(target),
        "size_bytes": len(content.encode("utf-8")),
        "missing_sections": missing,
    }
```

### 1.2 — Scanner agent (`agents/scanner.py`) (60 min)

```python
"""
agents/scanner.py
The Scanner agent: wraps fs_tools in a Foundry agent with function calling.
Produces a structured ScanResult that feeds into the Researcher.
"""

import os
import json
from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential
from azure.ai.projects.models import FunctionTool, ToolSet

from tools.fs_tools import scan_directory, check_path_tools, read_existing_context_file


SCANNER_INSTRUCTIONS = """
You are the Scanner agent for Fix My Vibe.

Your job is to analyse a developer's project directory and produce a complete diagnosis
of their AI coding tool setup. You use the tools available to you — never guess.

Process:
1. Call scan_directory(project_path) to get the file-based analysis
2. Call check_path_tools() to check what's installed on PATH
3. For any existing context files found, call read_existing_context_file() on each
4. Synthesise all results into a clear JSON diagnosis

Your output MUST be valid JSON with this structure:
{
  "detected_tools": ["claude_code", "cursor"],
  "detected_stack": ["python", "fastapi", "docker"],
  "is_monorepo": false,
  "security_issues": [...],
  "missing_configs": {"cursor": ".cursorrules"},
  "existing_configs": {"claude_code": {"audit": {...}}},
  "path_tools": {"aider": "aider"},
  "diagnosis_summary": "One paragraph plain English summary of what was found and what needs fixing",
  "priority": "high|medium|low"
}

Be specific. If Cursor is detected but .cursorignore is missing, flag it as high priority.
If .env is present without gitignore coverage, flag it as high severity.
Return ONLY the JSON — no markdown, no preamble.
"""


def get_scanner_tools() -> list:
    """Return the function tool definitions for the Scanner agent."""
    return [
        {
            "name": "scan_directory",
            "description": "Scan a project directory for AI tool config files, stack signatures, and security issues",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_path": {"type": "string", "description": "Absolute path to the project directory"}
                },
                "required": ["project_path"]
            }
        },
        {
            "name": "check_path_tools",
            "description": "Check which AI coding tools are installed on the system PATH",
            "parameters": {"type": "object", "properties": {}, "required": []}
        },
        {
            "name": "read_existing_context_file",
            "description": "Read and audit an existing context file (CLAUDE.md, .cursorrules, etc)",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_path": {"type": "string"},
                    "filename": {"type": "string", "description": "e.g. 'CLAUDE.md' or '.cursorrules'"}
                },
                "required": ["project_path", "filename"]
            }
        }
    ]


def run_scanner(client: AIProjectClient, project_path: str) -> dict:
    """
    Run the Scanner agent against project_path.
    Returns the parsed scan result dict.
    """
    # Define function tools
    functions = {
        "scan_directory": lambda args: scan_directory(args["project_path"]),
        "check_path_tools": lambda args: check_path_tools(),
        "read_existing_context_file": lambda args: read_existing_context_file(
            args["project_path"], args["filename"]
        ),
    }

    # Create the agent
    agent = client.agents.create_agent(
        model=os.environ.get("FOUNDRY_MODEL_DEPLOYMENT_NAME", "Phi-4-reasoning"),
        name="fix-my-vibe-scanner",
        instructions=SCANNER_INSTRUCTIONS,
        tools=[{"type": "function", "function": t} for t in get_scanner_tools()],
    )

    thread = client.agents.threads.create()
    client.agents.messages.create(
        thread_id=thread.id,
        role="user",
        content=f"Scan this project and produce a full diagnosis: {project_path}"
    )

    # Process run — handles tool calls automatically
    run = client.agents.runs.create_and_process(
        thread_id=thread.id,
        agent_id=agent.id,
        tool_handlers=_make_tool_handler(functions),
    )

    # Extract the response
    messages = client.agents.messages.list(thread_id=thread.id)
    result_text = ""
    for msg in messages:
        if msg.role == "assistant":
            result_text = msg.content[0].text.value
            break

    # Clean up agent
    client.agents.delete_agent(agent.id)

    # Parse JSON result
    try:
        return json.loads(result_text)
    except json.JSONDecodeError:
        # If model added markdown fences, strip them
        clean = result_text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        return json.loads(clean)


def _make_tool_handler(functions: dict):
    """Build a tool call handler for create_and_process."""
    def handler(tool_call):
        fn_name = tool_call.function.name
        args = json.loads(tool_call.function.arguments)
        if fn_name in functions:
            result = functions[fn_name](args)
            return json.dumps(result)
        return json.dumps({"error": f"Unknown tool: {fn_name}"})
    return handler
```

**Note on `create_and_process` with tool handlers:** The exact API for passing custom tool handlers may differ slightly by SDK version. Check the samples at https://github.com/Azure/azure-sdk-for-python/tree/main/sdk/ai/azure-ai-projects/samples/agents — look for `sample_agents_with_function_tool.py`. The pattern above is correct in shape; adjust the `tool_handlers` parameter name if the SDK uses a different kwarg.

### 1.3 — CLI skeleton (`cli.py`) (30 min)

```python
"""
cli.py
Fix My Vibe — main CLI entrypoint.
Usage: python cli.py scan <project_path>
"""

import os
import sys
import json
from dotenv import load_dotenv
from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential

load_dotenv()


def get_client() -> AIProjectClient:
    endpoint = os.environ.get("FOUNDRY_PROJECT_ENDPOINT")
    if not endpoint:
        print("ERROR: FOUNDRY_PROJECT_ENDPOINT not set in .env")
        sys.exit(1)
    return AIProjectClient(
        endpoint=endpoint,
        credential=DefaultAzureCredential(),
    )


def cmd_scan(project_path: str):
    """Run the full agent loop against a project directory."""
    from agents.scanner import run_scanner

    print(f"\n🔍 Fix My Vibe — scanning: {project_path}\n")
    client = get_client()

    print("[ 1/5 ] Scanner agent running...")
    scan_result = run_scanner(client, project_path)

    # Pretty print for now — Researcher/Planner come Day 2
    print("\n── Scan Result ──────────────────────────────")
    print(json.dumps(scan_result, indent=2))
    print("─────────────────────────────────────────────")
    print("\n✓ Scanner complete. Researcher/Planner coming Day 2.\n")


def main():
    if len(sys.argv) < 3:
        print("Usage: python cli.py scan <project_path>")
        sys.exit(1)

    command = sys.argv[1]
    if command == "scan":
        cmd_scan(sys.argv[2])
    else:
        print(f"Unknown command: {command}")
        sys.exit(1)


if __name__ == "__main__":
    main()
```

### 1.4 — Create a test fixture project (30 min)

This is a fake "bare" project you run the Scanner on to verify output. Build it now so you
have something real to test against. It should simulate the worst-case new vibe coder.

```bash
mkdir -p fixtures/bare-project/src
cd fixtures/bare-project

# Simulate a Python FastAPI project with no setup at all
cat > requirements.txt << 'EOF'
fastapi
uvicorn
sqlalchemy
python-dotenv
EOF

cat > main.py << 'EOF'
from fastapi import FastAPI
app = FastAPI()

@app.get("/")
def read_root():
    return {"Hello": "World"}
EOF

# The key: an .env file with no gitignore
cat > .env << 'EOF'
DATABASE_URL=postgresql://user:password123@localhost/mydb
SECRET_KEY=super-secret-key-do-not-share
API_KEY=sk-abc123
EOF

# No CLAUDE.md, no .gitignore, no .cursorrules — this is what Fix My Vibe should fix
echo "# My App" > README.md
```

Then test:
```bash
cd ../..  # back to fix-my-vibe root
python cli.py scan ./fixtures/bare-project
```

Expected output: scan result showing `python` stack detected, `.env` security issue flagged,
no AI tools detected (triggering PATH check), missing configs identified.

### 1.5 — End of Day 1 checklist

Before stopping:

- [ ] `python cli.py scan ./fixtures/bare-project` runs without error
- [ ] Scan result JSON includes: `detected_stack`, `security_issues`, `missing_configs`
- [ ] Foundry reasoning trace visible in Azure portal (check under your project → Agents → Runs)
- [ ] All code committed and pushed to GitHub
- [ ] `test_connection.py` still passes (confirm Foundry is stable)

```bash
git add .
git commit -m "feat: Scanner agent with 3-layer detection and CLI skeleton"
git push
```

---

## Reasoning Traces — demo feature

Phi-4-reasoning emits `<think>...</think>` blocks before every answer. These are stripped
automatically before JSON parsing (`parse_json_response` in `foundry_utils.py`), so they
never break output. But they are captured and surfaced when you run with `--verbose`:

```bash
python3 src/cli.py ./some-project --verbose
```

This prints each agent's full reasoning chain inline:

```
[ 3/5 ] Planner agent reasoning (Foundry)...

  ── Planner Reasoning ──────────────────────────────────────────────
  The project has a Python + FastAPI stack. I can see requirements.txt
  and main.py. There is a .env file present — checking .gitignore...
  .gitignore is missing. This is a high-severity security issue and
  must be rank 1. Cursor is detected via .cursorrules but there is no
  .cursorignore, meaning .env is readable by Cursor's AI context...
  ────────────────────────────────────────────────────────────────────
```

**Demo script:** Run with `--verbose` against the `tests/fixtures/bare-project` fixture.
The Planner trace is the money shot — it shows the agent reasoning through security issues,
tool detection evidence, and config generation decisions in real time. This directly hits
the Best Reasoning Agent judging criteria.

---

## Troubleshooting reference

**`DefaultAzureCredential` fails:** Run `az login` then retry. If in a corporate tenant,
try `az login --tenant <tenant-id>`.

**Model not found:** Check exact deployment name in Foundry portal → Models + Endpoints.
It's case-sensitive. Update `.env` to match exactly.

**`create_and_process` has no `tool_handlers` param:** Some SDK versions use a manual poll
loop instead. Check the SDK version: `pip show azure-ai-projects`. If <1.0, look at samples
for the older `submit_tool_outputs_to_run` pattern. Update with `pip install --upgrade azure-ai-projects`.

**Agent creates but run stays `queued`:** Phi-4-reasoning uses serverless API so quota is not
the issue. Check that the deployment name in `.env` matches exactly what's in Foundry portal →
Models + Endpoints (case-sensitive, e.g. `Phi-4-reasoning` not `phi-4-reasoning`).

**JSON parse error from Phi-4-reasoning:** The model emitted a `<think>` block that wasn't
stripped. Confirm you are on the latest `foundry_utils.py` — both `parse_json_response` and
`get_last_assistant_message` strip `<think>` blocks via regex before returning.

**Tavily `TAVILY_API_KEY` not found:** Confirm the key is in `.env` and `load_dotenv()` is
called before the Researcher agent runs. Key format is `tvly-...`.

---

*End of Day 0 / Day 1 plan — fix-my-vibe*
*Day 2 plan: Researcher (Bing Grounding) + Planner agents*
