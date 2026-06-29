"""
src/tracing.py
Lightweight timing/trace instrumentation for Fix My Vibe's Foundry pipeline.

Purpose: turn "the cloud pipeline is slow" into a per-phase, per-round-trip
breakdown so optimisation work is driven by numbers, not guesses. A single
traced run answers: which agent is slow, and is the time going into model
round-trips, polling, local tool execution, or agent provisioning?

Design:
- Zero behavioural change and near-zero overhead when disabled (the default).
- Enable explicitly via init_tracing(True) (the CLI --trace flag) or implicitly
  via the FMV_TRACE=1 env var (covers the MCP server without extra wiring).
- Every event is written as one JSONL line to .fmv-traces/<timestamp>.jsonl and
  kept in memory for the end-of-run summary table.

The active "phase" (scanner, researcher, ...) is tracked in a contextvar set by
timed(..., set_phase=True), so events recorded deep in foundry_utils auto-tag
with the agent that triggered them — no need to thread a phase argument through
every call site.
"""

import os
import json
import time
import logging
import contextvars
from pathlib import Path
from contextlib import contextmanager

log = logging.getLogger("fmv.trace")

_initialized = False
_enabled = False
_run_start = 0.0
_events: list[dict] = []
_fh = None
_trace_path: Path | None = None
_current_phase: contextvars.ContextVar = contextvars.ContextVar("fmv_phase", default="root")

# Order phases appear in the summary, regardless of first-seen order.
_PHASE_ORDER = ["scanner", "researcher", "planner", "remediator", "executor", "verifier"]


def _now() -> float:
    return time.perf_counter()


def init_tracing(enabled: bool, run_label: str = "") -> None:
    """Initialise tracing. Idempotent — the first call wins.

    Pass enabled=True (CLI --trace) to force it on. When called with
    enabled=False we still honour FMV_TRACE=1 from the environment, so the
    MCP server and other entry points opt in without code changes.
    """
    global _initialized, _enabled, _run_start, _fh, _trace_path, _events
    if _initialized:
        return
    _initialized = True

    _enabled = enabled or os.environ.get("FMV_TRACE", "").lower() in ("1", "true", "yes")
    if not _enabled:
        return

    _run_start = _now()
    _events = []

    # Configure logging if the app hasn't already, then make our spans + the
    # foundry tool-call DEBUG lines visible without flooding third-party loggers.
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    log.setLevel(logging.INFO)
    logging.getLogger("src.foundry_utils").setLevel(logging.DEBUG)

    ts = time.strftime("%Y%m%d-%H%M%S")
    out_dir = Path(os.environ.get("FMV_TRACE_DIR", ".fmv-traces"))
    out_dir.mkdir(parents=True, exist_ok=True)
    suffix = f"-{run_label}" if run_label else ""
    _trace_path = out_dir / f"{ts}{suffix}.jsonl"
    _fh = open(_trace_path, "w")
    log.info("tracing enabled — writing %s", _trace_path)
    record("run_start", label=run_label)

    # Print the summary on exit even if the pipeline raises — a crash mid-run
    # (e.g. a tool-handler KeyError) is exactly when the per-phase breakdown is
    # most useful. print_summary() is guarded against double-printing, so an
    # explicit caller on the happy path still wins.
    import atexit
    atexit.register(print_summary)


def _ensure_init() -> None:
    """Lazy init from env for entry points that never call init_tracing()."""
    if not _initialized:
        init_tracing(False)


def is_enabled() -> bool:
    _ensure_init()
    return _enabled


def record(kind: str, dur: float | None = None, **fields) -> None:
    """Append one structured event (in memory + JSONL). No-op when disabled."""
    if not _enabled:
        return
    event = {
        "t": round(_now() - _run_start, 4),
        "phase": _current_phase.get(),
        "kind": kind,
    }
    if dur is not None:
        event["dur"] = round(dur, 4)
    event.update(fields)
    _events.append(event)
    if _fh is not None:
        _fh.write(json.dumps(event) + "\n")
        _fh.flush()


@contextmanager
def timed(span: str, kind: str = "span", set_phase: bool = False, **fields):
    """Time a block and record it. When set_phase=True, `span` also becomes the
    active phase for any events recorded inside the block."""
    _ensure_init()
    if not _enabled:
        yield
        return
    token = _current_phase.set(span) if set_phase else None
    start = _now()
    try:
        yield
    finally:
        record(kind, dur=_now() - start, span=span, **fields)
        if token is not None:
            _current_phase.reset(token)


def get_trace_path() -> Path | None:
    return _trace_path


# ── Summary ──────────────────────────────────────────────────────────────────

def _fmt(seconds: float) -> str:
    return f"{seconds:.2f}s"


_printed = False


def print_summary(out=None) -> None:
    """Print a per-phase wall-time breakdown. No-op when disabled, empty, or
    already printed (so the atexit backstop and an explicit call don't double up)."""
    global _printed
    import sys
    out = out or sys.stderr
    if not _enabled or not _events or _printed:
        return
    _printed = True

    # Bucket events by phase.
    phases: dict[str, dict] = {}
    for ev in _events:
        p = ev.get("phase", "root")
        b = phases.setdefault(p, {
            "wall": 0.0, "run_loop": 0.0, "thread_create": 0.0,
            "model_rts": 0, "polls": 0, "poll_sleep": 0.0,
            "tool_count": 0, "tool_time": 0.0, "runs": 0,
        })
        kind = ev.get("kind")
        if kind == "phase":
            b["wall"] += ev.get("dur", 0.0)
        elif kind == "thread_create":
            b["thread_create"] += ev.get("dur", 0.0)
        elif kind == "run":
            b["run_loop"] += ev.get("dur", 0.0)
            b["runs"] += 1
            b["model_rts"] += ev.get("model_rts", 0)
            b["polls"] += ev.get("polls", 0)
            b["poll_sleep"] += ev.get("poll_sleep", 0.0)
            b["tool_count"] += ev.get("tool_count", 0)
            b["tool_time"] += ev.get("tool_time", 0.0)

    ordered = [p for p in _PHASE_ORDER if p in phases]
    ordered += [p for p in phases if p not in _PHASE_ORDER and p != "root"]

    if not ordered:
        # Only run_start (or pre-phase events) recorded — no phase ever ran.
        # Almost always: the run resolved to a mode/path with no instrumented
        # phases, or it errored before the first phase. Say so plainly instead
        # of printing a misleading all-zeros table.
        print("\n  Fix My Vibe — trace: no phases recorded "
              "(no instrumented agent ran — e.g. an error before phase 1).", file=out)
        if _trace_path is not None:
            print(f"  Event log: {_trace_path}\n", file=out)
        return

    header = (
        f"{'PHASE':<12}{'WALL':>9}{'MODEL_RTs':>11}{'POLLS':>7}"
        f"{'TOOLS':>7}{'TOOL_T':>9}{'PROVISION':>11}"
    )
    print("\n" + "=" * len(header), file=out)
    print("  Fix My Vibe — trace summary", file=out)
    print("=" * len(header), file=out)
    print(header, file=out)
    print("-" * len(header), file=out)

    totals = {"wall": 0.0, "model_rts": 0, "polls": 0, "tool_count": 0,
              "tool_time": 0.0, "provision": 0.0}
    for p in ordered:
        b = phases[p]
        # Residual: phase wall minus the instrumented run loop and thread setup.
        # This is create_agent + delete_agent + JSON parsing + glue — the fixed
        # provisioning tax paid per agent, per run.
        provision = max(0.0, b["wall"] - b["run_loop"] - b["thread_create"])
        totals["wall"] += b["wall"]
        totals["model_rts"] += b["model_rts"]
        totals["polls"] += b["polls"]
        totals["tool_count"] += b["tool_count"]
        totals["tool_time"] += b["tool_time"]
        totals["provision"] += provision
        print(
            f"{p:<12}{_fmt(b['wall']):>9}{b['model_rts']:>11}{b['polls']:>7}"
            f"{b['tool_count']:>7}{_fmt(b['tool_time']):>9}{_fmt(provision):>11}",
            file=out,
        )

    print("-" * len(header), file=out)
    print(
        f"{'TOTAL':<12}{_fmt(totals['wall']):>9}{totals['model_rts']:>11}"
        f"{totals['polls']:>7}{totals['tool_count']:>7}"
        f"{_fmt(totals['tool_time']):>9}{_fmt(totals['provision']):>11}",
        file=out,
    )
    print("=" * len(header), file=out)
    print("  MODEL_RTs = model round-trips (waits on the LLM).  "
          "PROVISION = create+delete agent + glue.", file=out)
    if _trace_path is not None:
        print(f"  Full event log: {_trace_path}", file=out)
    print("=" * len(header) + "\n", file=out)
