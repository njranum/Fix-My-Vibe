# demo-webapp

A tiny orders API, vibe-coded in an afternoon. Used as a Fix My Vibe demo
fixture: it intentionally ships with three fixable problems —

1. No `CLAUDE.md`, despite being built with Claude Code.
2. A `.env` full of secrets that isn't gitignored.
3. A hardcoded payment key in `app.py`.

```bash
pip install -r requirements.txt
flask --app app run
```
