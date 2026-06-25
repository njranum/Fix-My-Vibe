# KB response fixtures

Captured Azure AI Search responses, one file per remediable finding type, used by
the remediator contract tests (`tests/test_remediator.py`) so the query-building and
citation logic is tested against representative KB data WITHOUT CI needing Azure.

**These are SYNTHETIC placeholders** grounded in `kb/security-patterns/*.md`. Regenerate
them against the live index with:

    AZURE_SEARCH_ENDPOINT=... AZURE_SEARCH_KEY=... python scripts/capture_kb_responses.py

Each file is a JSON list of result objects: {title, url, content, threats, stacks}.
