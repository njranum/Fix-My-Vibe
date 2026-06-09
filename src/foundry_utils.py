"""
src/foundry_utils.py
Shared helpers for Azure AI Foundry agent interactions.
All agents use these to create/run/cleanup Foundry agents.
"""

import os
import re
import json
import time
import logging
from typing import Callable

log = logging.getLogger(__name__)


def get_client():
    """Build an AIProjectClient from environment variables."""
    from azure.ai.projects import AIProjectClient
    from azure.identity import DefaultAzureCredential

    endpoint = os.environ.get("FOUNDRY_PROJECT_ENDPOINT")
    if not endpoint:
        raise EnvironmentError("FOUNDRY_PROJECT_ENDPOINT not set — check your .env file")
    return AIProjectClient(
        endpoint=endpoint,
        credential=DefaultAzureCredential(),
    )


def run_agent_with_tools(
    client,
    agent_id: str,
    thread_id: str,
    tool_handlers: dict[str, Callable],
    max_iterations: int = 20,
) -> str:
    """
    Run a Foundry agent, handling function tool calls in a polling loop.
    Returns the final run status.
    """
    run = client.agents.runs.create(thread_id=thread_id, agent_id=agent_id)
    iterations = 0

    while run.status in ("queued", "in_progress", "requires_action") and iterations < max_iterations:
        iterations += 1

        if run.status == "requires_action":
            tool_calls = run.required_action.submit_tool_outputs.tool_calls
            outputs = []
            for tc in tool_calls:
                fn_name = tc.function.name
                try:
                    args = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    args = {}

                log.debug("Tool call: %s(%s)", fn_name, args)
                if fn_name in tool_handlers:
                    result = tool_handlers[fn_name](args)
                else:
                    result = {"error": f"Unknown tool: {fn_name}"}

                outputs.append({
                    "tool_call_id": tc.id,
                    "output": json.dumps(result),
                })

            run = client.agents.runs.submit_tool_outputs(
                thread_id=thread_id,
                run_id=run.id,
                tool_outputs=outputs,
            )
        else:
            time.sleep(0.5)
            run = client.agents.runs.get(thread_id=thread_id, run_id=run.id)

    return run.status


def extract_think_block(text: str) -> str:
    """Extract content from <think>...</think> tags emitted by Phi-4-reasoning."""
    match = re.search(r"<think>(.*?)</think>", text, re.DOTALL)
    return match.group(1).strip() if match else ""


def get_last_assistant_message(client, thread_id: str) -> str:
    """Extract the last assistant message text from a thread, stripping think blocks."""
    for msg in client.agents.messages.list(thread_id=thread_id):
        if msg.role == "assistant":
            raw = msg.content[0].text.value
            return re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
    return ""


def get_last_assistant_message_with_reasoning(client, thread_id: str) -> tuple[str, str]:
    """Return (clean_answer, reasoning_trace) from the last assistant message.

    The reasoning_trace is the raw content of the <think> block, suitable for
    surfacing as a visible reasoning trace in demos and --verbose output.
    """
    for msg in client.agents.messages.list(thread_id=thread_id):
        if msg.role == "assistant":
            raw = msg.content[0].text.value
            reasoning = extract_think_block(raw)
            clean = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
            return clean, reasoning
    return "", ""


def parse_json_response(text: str) -> dict:
    """Parse JSON from model output, stripping <think> blocks and markdown fences."""
    # Strip <think> blocks first — Phi-4-reasoning emits these before the JSON answer
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        cleaned = "\n".join(lines[1:-1]) if len(lines) > 2 else cleaned
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as e:
        return {"error": f"Failed to parse JSON: {e}", "raw": text}


def create_thread_and_send(client, message: str) -> str:
    """Create a thread, send a user message, return thread_id."""
    thread = client.agents.threads.create()
    client.agents.messages.create(thread_id=thread.id, role="user", content=message)
    return thread.id
