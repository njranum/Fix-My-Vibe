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

from src import tracing

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
    max_iterations: int = 120,
    max_retries: int = 3,
) -> str:
    """
    Run a Foundry agent, handling function tool calls in a polling loop.
    Rate-limited AND transient server errors are retried with backoff (a fresh run
    on the same thread). Retrying is safe: every agent that still uses an LLM is
    read-only / side-effect-free (the Executor writes deterministically, not here).
    Returns the final run status.
    """
    _RETRYABLE = {"rate_limited", "transient_error"}
    status = "rate_limited"
    for attempt in range(max_retries + 1):
        status = _run_once(client, agent_id, thread_id, tool_handlers, max_iterations)
        if status not in _RETRYABLE:
            return status
        # Rate limits need a real cool-off; transient server errors clear quickly.
        wait = (30 if status == "rate_limited" else 5) * (attempt + 1)
        reason = "rate limit hit" if status == "rate_limited" else "transient server error"
        print(f"  ⚠ Foundry {reason} — retrying in {wait}s "
              f"(attempt {attempt + 1}/{max_retries})")
        time.sleep(wait)
    raise RuntimeError(f"Foundry run failed: {status} persisted across {max_retries} retries")


def _run_once(client, agent_id, thread_id, tool_handlers, max_iterations) -> str:
    run = client.agents.runs.create(thread_id=thread_id, agent_id=agent_id)
    iterations = 0

    # Trace counters (only meaningful when tracing is enabled; cheap otherwise).
    _t_start = time.perf_counter()
    _model_rts = 1  # the initial run.create is the first wait on the model
    _polls = 0
    _poll_sleep = 0.0
    _tool_count = 0
    _tool_time = 0.0

    while str(run.status) in (
        "queued", "in_progress", "requires_action",
        "RunStatus.QUEUED", "RunStatus.IN_PROGRESS", "RunStatus.REQUIRES_ACTION",
        "<RunStatus.QUEUED: 'queued'>", "<RunStatus.IN_PROGRESS: 'in_progress'>",
        "<RunStatus.REQUIRES_ACTION: 'requires_action'>",
    ) and iterations < max_iterations:
        iterations += 1

        if str(run.status) in (
            "requires_action",
            "RunStatus.REQUIRES_ACTION",
            "<RunStatus.REQUIRES_ACTION: 'requires_action'>",
        ):
            tool_calls = run.required_action.submit_tool_outputs.tool_calls
            outputs = []
            for tc in tool_calls:
                fn_name = tc.function.name
                try:
                    args = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    args = {}

                log.debug("Tool call: %s(%s)", fn_name, args)
                _tc_start = time.perf_counter()
                if fn_name in tool_handlers:
                    result = tool_handlers[fn_name](args)
                else:
                    result = {"error": f"Unknown tool: {fn_name}"}
                _tc_dur = time.perf_counter() - _tc_start
                _tool_count += 1
                _tool_time += _tc_dur
                tracing.record("tool_call", dur=_tc_dur, name=fn_name)

                outputs.append({
                    "tool_call_id": tc.id,
                    "output": json.dumps(result),
                })

            run = client.agents.runs.submit_tool_outputs(
                thread_id=thread_id,
                run_id=run.id,
                tool_outputs=outputs,
            )
            _model_rts += 1  # submitting tool outputs resumes (waits on) the model
        else:
            time.sleep(0.5)
            _poll_sleep += 0.5
            _polls += 1
            run = client.agents.runs.get(thread_id=thread_id, run_id=run.id)

    tracing.record(
        "run", dur=time.perf_counter() - _t_start,
        model_rts=_model_rts, polls=_polls, poll_sleep=round(_poll_sleep, 3),
        tool_count=_tool_count, tool_time=round(_tool_time, 4),
        iterations=iterations, final_status=str(run.status),
    )

    if hasattr(run, "last_error") and run.last_error:
        error_code = (
            run.last_error.get("code") if isinstance(run.last_error, dict)
            else getattr(run.last_error, "code", None)
        )
        code = str(error_code)
        if code == "rate_limit_exceeded":
            return "rate_limited"  # caller retries with backoff
        # Transient server-side failures clear on a fresh run — signal a retry
        # instead of aborting the whole pipeline (observed: 'server_error' on the
        # Researcher killed an otherwise-healthy run mid-demo).
        if code in ("server_error", "service_unavailable", "server_unavailable"):
            log.warning("Transient Foundry error (%s) — will retry", code)
            return "transient_error"
        log.error("Run failed: %s", run.last_error)
        raise RuntimeError(f"Foundry run failed: {run.last_error}")
    if iterations >= max_iterations:
        # Loop exhausted while the run was still going — surface it loudly,
        # otherwise the caller sees an empty final message and no clue why.
        log.error("Run %s still %s after %d iterations — giving up", run.id, run.status, iterations)
        print(f"  ⚠ Foundry run did not finish within {max_iterations} polling iterations "
              f"(status: {run.status}) — results may be incomplete")
    return run.status


def extract_think_block(text: str) -> str:
    """Extract content from <think>...</think> tags emitted by Phi-4-reasoning."""
    match = re.search(r"<think>(.*?)</think>", text, re.DOTALL)
    return match.group(1).strip() if match else ""


def _message_text(msg) -> str:
    """Safely pull text from a message's content parts, or '' if it has none.

    Assistant turns can carry empty content (e.g. a tool-call-only message), in
    which case ``msg.content[0]`` raises IndexError. Walk the parts and return the
    first one that actually has text, so callers can skip empty turns.
    """
    try:
        for part in msg.content:
            text = getattr(part, "text", None)
            value = getattr(text, "value", None)
            if value:
                return value
    except (AttributeError, TypeError):
        pass
    return ""


def get_last_assistant_message(client, thread_id: str) -> str:
    """Extract the last assistant message text from a thread, stripping think blocks."""
    for msg in client.agents.messages.list(thread_id=thread_id):
        if msg.role == "assistant":
            raw = _message_text(msg)
            if raw:
                return re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
    return ""


def get_last_assistant_message_with_reasoning(client, thread_id: str) -> tuple[str, str]:
    """Return (raw_text, reasoning_trace) from the last assistant message.

    Phi-4-reasoning emits chain-of-thought as plain text before the JSON.
    We return the full raw text (parse_json_response handles extraction) and
    treat everything before the final { as the reasoning trace for --verbose.
    Empty assistant turns (no text content) are skipped.
    """
    for msg in client.agents.messages.list(thread_id=thread_id):
        if msg.role == "assistant":
            raw = _message_text(msg)
            if not raw:
                continue
            # Try <think> block first; fall back to text before the last JSON {
            reasoning = extract_think_block(raw)
            if not reasoning:
                last_brace = raw.rfind("{")
                reasoning = raw[:last_brace].strip() if last_brace > 0 else ""
            return raw, reasoning
    return "", ""


def parse_json_response(text: str) -> dict:
    """Parse JSON from model output.

    Phi-4-reasoning via the inference API emits its chain-of-thought as plain text
    before the JSON answer (no <think> tags). We try several extraction strategies
    in order, returning the first that produces valid JSON.
    """
    # Strategy 1: strip <think> blocks (used by some model configs) and parse directly
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        cleaned = "\n".join(lines[1:-1]) if len(lines) > 2 else cleaned
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Strategy 2: find the last {...} block in the text — model puts JSON at the end
    last_brace = text.rfind("{")
    if last_brace != -1:
        candidate = text[last_brace:]
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass

    # Strategy 3: extract first {...} block via regex (handles embedded JSON)
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    return {"error": "Failed to parse JSON from model response", "raw": text[:500]}


def create_thread_and_send(client, message: str) -> str:
    """Create a thread, send a user message, return thread_id."""
    with tracing.timed("thread_create", kind="thread_create"):
        thread = client.agents.threads.create()
        client.agents.messages.create(thread_id=thread.id, role="user", content=message)
        return thread.id
