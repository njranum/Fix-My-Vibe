"""
scripts/setup_kb.py
One-time setup: upload the curated security-patterns knowledge base to Azure
AI Foundry and create a vector store for the Researcher agent's file_search tool.

Usage:
    az login
    .venv/bin/python3 scripts/setup_kb.py

Writes FOUNDRY_KB_VECTOR_STORE_ID to .env (updates the line if present).
Re-running replaces the vector store (old one is deleted) so KB edits are easy
to publish.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

KB_DIR = Path(__file__).parent.parent / "kb" / "security-patterns"
ENV_FILE = Path(__file__).parent.parent / ".env"
ENV_KEY = "FOUNDRY_KB_VECTOR_STORE_ID"
VECTOR_STORE_NAME = "fix-my-vibe-security-kb"


def _update_env_file(vector_store_id: str) -> None:
    lines = ENV_FILE.read_text().splitlines() if ENV_FILE.exists() else []
    lines = [l for l in lines if not l.startswith(f"{ENV_KEY}=")]
    lines.append(f"{ENV_KEY}={vector_store_id}")
    ENV_FILE.write_text("\n".join(lines) + "\n")


def main() -> None:
    from dotenv import load_dotenv
    load_dotenv()
    from src.foundry_utils import get_client

    kb_files = sorted(KB_DIR.glob("*.md"))
    if not kb_files:
        print(f"No KB documents found in {KB_DIR}", file=sys.stderr)
        sys.exit(1)

    client = get_client()

    # Replace any previous vector store with the same name (KB re-publish)
    for vs in client.agents.vector_stores.list():
        if vs.name == VECTOR_STORE_NAME:
            print(f"Deleting previous vector store {vs.id}")
            client.agents.vector_stores.delete(vs.id)

    file_ids: list[str] = []
    for kb_file in kb_files:
        uploaded = client.agents.files.upload_and_poll(
            file_path=str(kb_file), purpose="assistants"
        )
        file_ids.append(uploaded.id)
        print(f"Uploaded {kb_file.name} -> {uploaded.id}")

    vector_store = client.agents.vector_stores.create_and_poll(
        file_ids=file_ids, name=VECTOR_STORE_NAME
    )
    print(f"Vector store ready: {vector_store.id} ({len(file_ids)} documents)")

    _update_env_file(vector_store.id)
    print(f"{ENV_KEY} written to .env")


if __name__ == "__main__":
    main()
