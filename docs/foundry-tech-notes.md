# Fix My Vibe — Foundry Technical Notes

## Model

All agents use **o4-mini** via Azure AI Foundry (serverless API).

- Deployment name: `o4-mini` (set in `FOUNDRY_MODEL_DEPLOYMENT_NAME`)
- o4-mini is a reasoning model: it produces chain-of-thought before answering
- o4-mini supports function calling via the Agents API — this is the core of the agentic design
- Reasoning traces surface automatically from the model's decision-making process

## SDK

Package: `azure-ai-projects==1.0.0b11`

Key classes used:
```python
from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential

client = AIProjectClient(endpoint=endpoint, credential=DefaultAzureCredential())

client.agents.create_agent(model, name, instructions, tools)
client.agents.threads.create()
client.agents.messages.create(thread_id, role, content)
client.agents.runs.create(thread_id, agent_id)
client.agents.runs.get(thread_id, run_id)
client.agents.runs.submit_tool_outputs(thread_id, run_id, tool_outputs)
client.agents.messages.list(thread_id)
client.agents.delete_agent(agent_id)
```

## Agentic Tool Call Loop

Every agent that has tools follows this pattern in `foundry_utils.run_agent_with_tools()`:

```
create_agent(tools=[...])
create_thread()
create_message(user prompt)
create_run()

while status in (queued, in_progress, requires_action):
    if requires_action:
        → o4-mini has decided to call a tool
        → execute the tool locally in Python
        → submit_tool_outputs() back to the model
    else:
        → sleep 0.5s, poll run status

get_last_assistant_message()  → final answer (JSON)
delete_agent()
```

The model decides WHICH tools to call, WHEN, and WITH WHAT arguments. Python only executes the tool and returns results. This is the genuine agentic pattern.

## What Each Agent Does

| Agent | Tools available | What the model decides |
|-------|----------------|------------------------|
| Scanner | scan_directory, check_path_tools, check_vscode_extensions, read_existing_context_file, infer_project_conventions | Which tools to call, in what order, whether to dig deeper |
| Researcher | search_web (Tavily) | What queries to run, for which tools, how many searches |
| Planner | none (pure reasoning) | What files to create, what content to generate — writes actual CLAUDE.md etc. |
| Executor | write_file | Which files to write (from confirmed plan) |
| Verifier | verify_file, read_existing_context_file | Which files to check, what quality issues to flag |

## Agent Pattern (per agent)

```python
def run_with_foundry(client, input_data) -> dict:
    agent = client.agents.create_agent(
        model=os.environ["FOUNDRY_MODEL_DEPLOYMENT_NAME"],
        name="agent-name",
        instructions=AGENT_INSTRUCTIONS,
        tools=_get_tool_definitions(),
    )
    thread_id = create_thread_and_send(client, json.dumps(input_data))
    run_agent_with_tools(client, agent.id, thread_id, _make_tool_handlers(...))
    raw, reasoning = get_last_assistant_message_with_reasoning(client, thread_id)
    result = parse_json_response(raw)
    result["_reasoning_trace"] = reasoning
    client.agents.delete_agent(agent.id)
    return result
```

## Local Fallback

Every agent has a `run(input: dict) -> dict` method that works without Azure:
```bash
python3 src/cli.py <path> --local   # never touches Azure, no model calls
```
The local mode uses Python logic for detection and Python templates for file generation. It runs in ~3 seconds. Use for development and testing.

## Authentication

Uses `DefaultAzureCredential` — picks up `az login` tokens automatically.

```bash
az login   # run before each session
az account show   # verify correct subscription
```

## Environment Variables

```
FOUNDRY_PROJECT_ENDPOINT=https://<resource>.services.ai.azure.com/api/projects/<project>
FOUNDRY_MODEL_DEPLOYMENT_NAME=o4-mini
TAVILY_API_KEY=tvly-...
```

Always load via `python-dotenv`. Always use `.venv/bin/python3`, not system `python3` (Homebrew PEP 668 isolation means system Python has no packages).

## Web Search

Tavily provides web search for the Researcher agent.

- Package: `tavily-python>=0.3.0`
- Used by Researcher agent only
- Falls back to static knowledge base in `--local` mode

## Reasoning Traces

o4-mini produces chain-of-thought reasoning before its final answer. This comes through in two ways:

1. **Tool call decisions** — the model's reasoning about which tools to call is visible in the run's step details
2. **Final answer** — the text before the JSON answer contains the model's reasoning summary

`get_last_assistant_message_with_reasoning()` extracts both the answer and the reasoning trace.
Reasoning traces surface in `--verbose` mode via `_print_reasoning_trace()` in orchestrator.

Unlike Phi-4-reasoning (which used `<think>` tags or plain text reasoning), o4-mini's reasoning is natively integrated with its function calling decisions — the model reasons ABOUT tool results as they come in.

## JSON Output Convention

All agents return structured JSON as their final message. Instructions explicitly state "return ONLY the JSON — no markdown fences, no preamble."

`parse_json_response()` has three fallback extraction strategies:
1. Strip markdown fences, parse directly
2. Find last `{...}` block (model may prefix with reasoning text)
3. Regex extract first `{...}` block

## Cleanup

Agents are deleted after each run to avoid quota/limit buildup:
```python
client.agents.delete_agent(agent.id)
```
No persistent agents exist between runs. Thread history is not retained.
