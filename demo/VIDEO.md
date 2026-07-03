# Fix My Vibe — Screen-Recording Runbook

The exact steps to record the demo video and cut its assets. This consolidates two
earlier planning conversations: the command-level prep (`tree` flags, run location, the
diff commands) and the recording staging (tmux layout, GIF-vs-video, annotations, tools).

For the *narration* / what-to-say, see `demo/DEMO.md`. For the *deliverable list*, see
`docs/media/README.md`. This file is the **how to record** layer that sat between them.

---

## Assets you're producing (cut from ONE clean take)

Record a single clean full take as the source of truth, then cut everything from it —
don't record the pieces separately.

| Asset | Length | What it is |
|---|---|---|
| `demo-e2e.mp4` | ~75s | The hero. Full run; real-time at the decision moments, speed-ramped 6–8× through agent "thinking". Linked, not autoplayed. |
| `mcp-setup.gif` | ~12s | **Only** `claude mcp add` → `claude mcp list` showing `✔ Connected`. The GIF is the setup loop, not the whole pipeline. |
| `elicitation-prompt.png` | — | The checkbox confirmation gate. |
| `after-diff.png` | — | `diff app.py.bak app.py` showing the fixes land. |

---

## Before recording (setup)

1. **Terminal:** bump the font to **~16–18pt** so it's legible when embedded.
2. **Interpreter / MCP:** activate the venv and confirm the server is up, so you don't
   silently drop to local mode (no Foundry IQ) on camera:
   ```bash
   source .venv/bin/activate
   bash demo/preflight.sh        # deps, Foundry endpoint, KB, fixture — no cloud calls
   ```
   In Claude Code, `/mcp` should show `fix-my-vibe ✔ Connected`.
3. **One rehearsal run** (the only real proof Azure auth + KB respond, ~1.5 min):
   ```bash
   bash demo/run.sh --yes
   ```
4. **Reset to a clean "before" state** for the left pane:
   ```bash
   rm -rf .fmv-run/shop-api        # /fix-my-vibe recreates this fresh when it runs
   ```

### tmux layout

- **Left pane, ~30%** — the "before" view. Run:
  ```bash
  tree -a -I '.git' .fmv-run/shop-api
  ```
  `-a` shows hidden files (so the committed **`.env`** is visible — the whole point);
  `-I '.git'` hides the noisy `.git` dir. Useful variants: `tree -a -L 2` (limit depth),
  `tree -a --gitignore` (respect `.gitignore`, newer `tree` only).
  > `.fmv-run/shop-api` won't exist until `/fix-my-vibe` copies it. Either let the command
  > create it and *then* run `tree`, or pre-seed with
  > `cp -R tests/fixtures/demo-shop .fmv-run/shop-api` for the opening shot.
- **Right pane, ~70%** — a fresh Claude Code session.
- **Keep the split fixed** — mid-recording resizes look janky.

### Where the demo runs (don't mix these up)

- **`tests/fixtures/demo-shop`** — the **source** fixture. Checked into git, holds the 5
  built-in problems, never modified. Edit problems *here*.
- **`.fmv-run/shop-api`** — the **throwaway working copy**. Gitignored, overwritten every
  run. You point the tool *here*. All three MCP calls take the same absolute path:
  `<repo-root>/.fmv-run/shop-api`.

### Optional — fake-typing for clean keystrokes

If you want commands to type in character-by-character instead of pasting:
```bash
source demo-magic.sh
TYPE_SPEED=20
pe "fix-my-vibe ./tests/fixtures/demo-shop"   # types it out, waits for Enter, then runs it
```
Alternatives: `doitlive`, `pv -qL 20` (echo only, doesn't run), or `asciinema` for a
whole-session capture.

---

## During recording

1. **Setup shot (feeds the GIF):** fresh terminal —
   ```bash
   claude mcp add fix-my-vibe -- fix-my-vibe-mcp
   claude mcp list        # → fix-my-vibe ✔ Connected
   ```
   Stop here for `mcp-setup.gif`.
2. **Main take:** the tmux split. Left shows `tree -a -I '.git' .fmv-run/shop-api`
   (`.env`, `app.py`, no `CLAUDE.md`). In the right pane, type:
   ```
   /fix-my-vibe
   ```
   Let it run fully, untouched — including the **real checkbox tick** at the confirmation
   gate. Keep that moment at real speed; it's the product thesis.
3. **After shot:** re-run `tree` in the left pane to reveal the new `CLAUDE.md`,
   `SECURITY.md`, `.gitignore`; show the diff on the right (below).

### The ending reveal (diff beats nvim for legibility)

```bash
cd .fmv-run/shop-api
diff -u app.py.bak app.py      # red hardcoded key → green os.environ, debug flip, param query
bat SECURITY.md CLAUDE.md       # the freshly generated config, syntax-highlighted
```

Other diff angles (the working copy is **not** a git repo — it's a plain `cp -R` — so
`git diff` shows nothing; use `--no-index` or file-level diffs):
```bash
# whole-tree before/after (source vs working copy)
diff -ruN tests/fixtures/demo-shop .fmv-run/shop-api
# colorized red/green on camera despite the gitignore
git diff --no-index tests/fixtures/demo-shop .fmv-run/shop-api
# quick "are they identical yet" check (empty before apply)
diff -rq tests/fixtures/demo-shop .fmv-run/shop-api
```

New files to expect after `apply_fixes`: `CLAUDE.md`, `SECURITY.md`, `.gitignore`
(and `.cursorrules` / `.cursorignore` if Cursor is detected), plus `.bak` backups for any
file edited in place (e.g. `app.py.bak`).

---

## In post

- Trim to **~75s**; speed-ramp the waiting stretches 6–8×, hold real speed at the
  checkbox gate.
- Add lower-third stage captions:
  `1 · Scan → 2 · Research → 3 · Plan → ⏸ You confirm → 4 · Execute → 5 · Verify`.
- Add one callout on the citations: *"OWASP — retrieved from Azure AI Search, not the
  model's memory."*
- Grab `elicitation-prompt.png` and `after-diff.png` as stills from frames.

### Tools (macOS)

- **Screen Studio** (recommended) — auto-zoom on the active pane, speed-ramping, captions,
  exports MP4 + GIF.
- Fallback: QuickTime capture + `gifski` (GIF) + iMovie (captions).

---

## Open decisions (not yet wired into the harness)

- **`git init` on the demo copy** — currently the working copy is a plain `cp -R`, so
  `git status` / `git diff` don't work on it. If you want a git before/after in the video,
  the harness would need to `git init` + commit the copy first. Not done.
- **`tree` in the harness** — the `tree` shots are a manual presentation step; nothing
  scripts them.
- **nvim vs diff for the ending** — this runbook uses `diff` + `bat`; opening nvim works
  too, just less legible on camera.
