# Demo media

Drop the demo assets referenced by the top-level `README.md` here. Suggested set,
in priority order:

| File | Type | What it should show | Length |
|------|------|--------------------|--------|
| `demo-e2e.mp4` | Video | The hero clip: in Claude Code, ask "fix my vibe" on a messy project → `scan_project` findings → `apply_fixes` elicitation checkboxes → only ticked files written → verified. | 60–90s |
| `mcp-setup.gif` | GIF | `claude mcp add` then `claude mcp list` showing `fix-my-vibe ✔ Connected`, and the three tools available in a session. | 10–15s |
| `elicitation-prompt.png` | Screenshot | The confirmation checkbox prompt (one box per proposed fix) — the safety gate. | — |
| `cli-run.png` | Screenshot | A terminal `fix-my-vibe <path> --local` run summary. | — |
| `generated-security-md.png` | Screenshot | An example generated `SECURITY.md` audit report. | — |

Tips:
- Record against a throwaway copy of a fixture (`cp -r tests/fixtures/vulnerable-project /tmp/fmv-demo`)
  so nothing real is touched.
- For the hero video, local mode is safest for a live demo (offline, deterministic);
  pre-record Foundry mode separately if you want to show the grounded KB output.
- Keep the GIF small (≤ 5 MB) so it renders inline on GitHub.
