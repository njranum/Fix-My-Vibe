#!/usr/bin/env python3
"""
ingest_security_kb.py

Ingests curated security sources into Azure AI Search index for Fix My Vibe.
- Fetches documents from sources.json
- Chunks intelligently (by threat/pattern, ~500 tokens per chunk)
- Adds metadata (threat category, OWASP mapping, stack applicability)
- Generates embeddings (batched for efficiency)
- Uploads to Azure AI Search

Usage:
    python ingest_security_kb.py --config kb_config.json --sources kb/sources.json

Requires:
    - Azure AI Search resource
    - Azure OpenAI embeddings endpoint
    - Environment variables: AZURE_SEARCH_ENDPOINT, AZURE_SEARCH_KEY, AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_KEY
"""

import json
import logging
import sys
from pathlib import Path
from typing import Any, Optional
from dataclasses import dataclass, asdict
from datetime import datetime
import re
import hashlib
from html.parser import HTMLParser

import requests
from azure.core.credentials import AzureKeyCredential
from azure.search.documents import SearchClient
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import (
    SearchIndex,
    SearchField,
    SearchFieldDataType,
    SimpleField,
    SearchableField,
    VectorSearch,
    HnswAlgorithmConfiguration,
    VectorSearchProfile,
    SemanticConfiguration,
    SemanticField,
    SemanticPrioritizedFields,
    SemanticSearch,
)
from openai import AzureOpenAI
from dotenv import load_dotenv
import os

load_dotenv()


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

MAX_CONTENT_CHARS = 200_000  # ~50k tokens; protects against huge pages


# UI words that appear as standalone lines on modern doc sites (icons, buttons, nav labels).
# Exact-match only — we don't want to strip these words from mid-sentence prose.
_UI_NOISE_LINES = frozenset({
    "link", "menu", "expand", "collapse", "search", "copy", "copied",
    "document", "external link", "skip to main content", "skip to content",
    "table of contents", "on this page", "in this article",
    "edit this page", "edit page", "view source", "print",
    "next", "previous", "back to top",
    "light", "dark", "auto",  # theme toggles
})


class _HTMLStripper(HTMLParser):
    """Minimal HTML-to-text converter using stdlib only."""
    # Non-void block-level and UI tags whose text content is navigation/chrome, not prose.
    # Avoid void elements (link, meta, br, etc.) — they never get endtag events,
    # so including them would permanently set _skip_depth > 0 and eat all content.
    _SKIP_TAGS = {
        "script", "style", "nav", "head", "footer",
        "button", "aside", "header",
        "svg", "figure", "figcaption",
        "noscript", "template", "iframe",
    }

    def __init__(self):
        super().__init__()
        self._parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP_TAGS:
            self._skip_depth += 1

    def handle_endtag(self, tag):
        if tag in self._SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data):
        if self._skip_depth == 0:
            self._parts.append(data)

    def get_text(self) -> str:
        text = "\n".join(self._parts)
        return _clean_text(text)


def _clean_text(text: str) -> str:
    """Remove navigation noise and normalise whitespace from stripped HTML."""
    # Collapse runs of spaces/tabs on each line first
    text = re.sub(r"[ \t]{2,}", " ", text)
    lines = text.splitlines()
    cleaned = []
    for line in lines:
        stripped = line.strip()
        # Drop blank lines and single-character lines (stray bullets, icons)
        if len(stripped) < 2:
            continue
        # Drop standalone UI labels that docs sites render as icon+text
        if stripped.lower() in _UI_NOISE_LINES:
            continue
        cleaned.append(stripped)
    # Collapse 2+ consecutive blank lines (now represented as empty strings) — not needed
    # since we already dropped blanks, but re-join with single newlines and add paragraph
    # breaks where original had multiple blank lines.
    result = "\n".join(cleaned)
    # Restore paragraph spacing where there were genuine section breaks
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result.strip()


def strip_html(content: str) -> str:
    """Return plain text from HTML, or the original content if it isn't HTML."""
    stripped = content.lstrip()
    if not (stripped.startswith("<!") or stripped.startswith("<html") or stripped.startswith("<HTML")):
        # Heuristic: real HTML starts with a doctype or <html>
        # For other pages (OWASP, NIST) that omit doctype, check for <body>
        if "<body" not in content[:2000].lower():
            return content
    parser = _HTMLStripper()
    try:
        parser.feed(content)
        return parser.get_text()
    except Exception:
        return _clean_text(content)


@dataclass
class DocumentChunk:
    """Single chunk of indexed content."""
    id: str
    content: str
    source_url: str
    source_title: str
    threat_categories: list[str]
    owasp_mappings: list[str]
    stack_applicable_to: list[str]
    chunk_index: int
    source_publication_date: str
    embeddings: Optional[list[float]] = None


def _read_local_path(local_path: Path) -> Optional[str]:
    """Read a local file or all text files in a directory."""
    if local_path.is_dir():
        files = sorted(
            p for p in local_path.rglob("*")
            if p.is_file() and p.suffix.lower() in {".md", ".txt", ".rst"}
        )
        if not files:
            logger.warning(f"No readable files found in directory: {local_path}")
            return None
        parts = []
        for file_path in files:
            parts.append(f"# {file_path.name}\n\n{file_path.read_text()}")
        logger.info(f"  Loaded {len(files)} files from {local_path}")
        return "\n\n---\n\n".join(parts)

    if not local_path.is_file():
        logger.warning(f"Local path not found: {local_path}")
        return None

    return local_path.read_text()


def fetch_document(url: str, max_retries: int = 3) -> Optional[str]:
    """Fetch document content from URL with retries. Returns plain text (HTML stripped)."""
    if url.startswith("local://"):
        local_root = os.getenv("LOCAL_KB_ROOT", "/Users/snoopy/Dev")
        local_path = Path(local_root) / url.removeprefix("local://")
        try:
            return _read_local_path(local_path)
        except OSError as e:
            logger.warning(f"Failed to read local path {local_path}: {e}")
            return None

    for attempt in range(max_retries):
        try:
            headers = {"User-Agent": "Mozilla/5.0 (Fix-My-Vibe-KB-Ingestion/1.0)"}
            response = requests.get(url, headers=headers, timeout=15)
            response.raise_for_status()
            text = strip_html(response.text)
            if len(text) > MAX_CONTENT_CHARS:
                logger.warning(f"Truncating {url} from {len(text):,} to {MAX_CONTENT_CHARS:,} chars")
                text = text[:MAX_CONTENT_CHARS]
            return text
        except requests.RequestException as e:
            logger.warning(f"Fetch attempt {attempt + 1}/{max_retries} failed for {url}: {e}")
            if attempt == max_retries - 1:
                logger.error(f"Failed to fetch {url} after {max_retries} attempts")
                return None
    return None


def smart_chunk_content(
    content: str,
    threat_categories: list[str],
    chunk_size: int = 1500,  # ~500 tokens
    overlap: int = 200,
) -> list[str]:
    """Chunk content into ~500-token pieces with overlap, breaking on newlines where possible."""
    chars_per_chunk = chunk_size * 4
    overlap_chars = overlap * 4
    chunks = []
    start = 0

    while start < len(content):
        end = min(start + chars_per_chunk, len(content))

        # Prefer breaking at a newline so we don't cut mid-sentence
        if end < len(content):
            newline = content.rfind("\n", start, end)
            if newline > start:
                end = newline

        chunk = content[start:end].strip()
        if len(chunk) > 100:
            chunks.append(chunk)

        if end >= len(content):
            break

        # Advance by chunk_size minus overlap so adjacent chunks share context
        next_start = end - overlap_chars
        start = next_start if next_start > start else end  # guard against no-progress

    return chunks


def generate_embeddings(
    client: AzureOpenAI,
    texts: list[str],
    batch_size: int = 100,
) -> list[list[float]]:
    """Generate embeddings for texts using Azure OpenAI (batched)."""
    all_embeddings = []
    
    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        try:
            response = client.embeddings.create(
                input=batch,
                model=os.getenv("AZURE_OPENAI_EMBEDDING_MODEL", "text-embedding-3-small"),
            )
            all_embeddings.extend([item.embedding for item in response.data])
            logger.info(f"Generated embeddings for batch {i//batch_size + 1}")
        except Exception as e:
            logger.error(f"Failed to generate embeddings: {e}")
            raise
    
    return all_embeddings


def ingest_sources(
    sources_file: str,
    config_file: str,
    skip_existing: bool = False,
) -> None:
    """Main ingestion pipeline."""
    
    # Load sources
    with open(sources_file, "r") as f:
        sources_data = json.load(f)
    
    # Load config
    with open(config_file, "r") as f:
        config = json.load(f)
    
    # Initialize Azure clients
    search_endpoint = os.getenv("AZURE_SEARCH_ENDPOINT")
    search_key = os.getenv("AZURE_SEARCH_KEY")
    openai_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
    openai_key = os.getenv("AZURE_OPENAI_KEY")

    if not all([search_endpoint, search_key, openai_endpoint, openai_key]):
        logger.error("Missing required environment variables")
        sys.exit(1)

    search_credential = AzureKeyCredential(search_key)
    search_index_client = SearchIndexClient(search_endpoint, search_credential)
    openai_client = AzureOpenAI(api_key=openai_key, api_version="2024-10-21", azure_endpoint=openai_endpoint)
    
    # Create index if not exists
    index_name = config.get("index_name", "fix-my-vibe-security-kb")
    try:
        search_index_client.get_index(index_name)
        logger.info(f"Index '{index_name}' already exists")
    except:
        logger.info(f"Creating index '{index_name}'...")
        index = SearchIndex(
            name=index_name,
            fields=[
                SimpleField(name="id", type=SearchFieldDataType.String, key=True),
                SearchableField(name="content", type=SearchFieldDataType.String, retrievable=True),
                SimpleField(name="source_url", type=SearchFieldDataType.String, retrievable=True),
                SimpleField(name="source_title", type=SearchFieldDataType.String, retrievable=True),
                SimpleField(name="threat_categories", type=SearchFieldDataType.Collection(SearchFieldDataType.String), retrievable=True),
                SimpleField(name="owasp_mappings", type=SearchFieldDataType.Collection(SearchFieldDataType.String), retrievable=True),
                SimpleField(name="stack_applicable_to", type=SearchFieldDataType.Collection(SearchFieldDataType.String), retrievable=True),
                SimpleField(name="chunk_index", type=SearchFieldDataType.Int32, retrievable=True),
                SimpleField(name="source_publication_date", type=SearchFieldDataType.String, retrievable=True),
                SearchField(
                    name="embeddings",
                    type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
                    searchable=True,
                    vector_search_dimensions=1536,
                    vector_search_profile_name="myHnswProfile",
                ),
            ],
            vector_search=VectorSearch(
                algorithms=[HnswAlgorithmConfiguration(name="myHnsw")],
                profiles=[VectorSearchProfile(name="myHnswProfile", algorithm_configuration_name="myHnsw")],
            ),
            semantic_search=SemanticSearch(
                configurations=[
                    SemanticConfiguration(
                        name="default",
                        prioritized_fields=SemanticPrioritizedFields(
                            content_fields=[SemanticField(field_name="content")],
                            keywords_fields=[SemanticField(field_name="threat_categories")],
                        ),
                    )
                ]
            ),
        )
        search_index_client.create_index(index)
        logger.info(f"Index '{index_name}' created successfully")
    
    # Initialize search client
    search_client = SearchClient(search_endpoint, index_name, search_credential)
    
    # Process sources one at a time — embed and upload immediately to avoid
    # buffering all chunks + embeddings (~600 docs) in RAM simultaneously.
    total_chunks = 0
    failed_sources = 0

    for source in sources_data.get("sources", []):
        source_id = source["id"]
        logger.info(f"Processing: {source['title']}")

        if skip_existing:
            first_chunk_id = hashlib.md5(f"{source_id}-0".encode()).hexdigest()[:12]
            try:
                search_client.get_document(key=first_chunk_id)
                logger.info(f"  Skipping (already indexed): {source['title']}")
                continue
            except Exception:
                pass  # Not found — proceed with ingestion

        content = fetch_document(source["url"])
        if not content:
            logger.warning(f"  Skipping (fetch failed): {source['title']}")
            failed_sources += 1
            continue

        chunk_texts = smart_chunk_content(content, source.get("threat_categories", []))
        logger.info(f"  Created {len(chunk_texts)} chunks")

        if not chunk_texts:
            logger.warning(f"  No usable chunks from {source['title']}, skipping")
            continue

        chunk_docs: list[DocumentChunk] = [
            DocumentChunk(
                id=hashlib.md5(f"{source_id}-{idx}".encode()).hexdigest()[:12],
                content=text,
                source_url=source["url"],
                source_title=source["title"],
                threat_categories=source.get("threat_categories", []),
                owasp_mappings=source.get("threat_categories", []),
                stack_applicable_to=source.get("stack_applicable_to", []),
                chunk_index=idx,
                source_publication_date=source.get("publication_date", ""),
            )
            for idx, text in enumerate(chunk_texts)
        ]

        embeddings = generate_embeddings(openai_client, [c.content for c in chunk_docs])
        for chunk, embedding in zip(chunk_docs, embeddings):
            chunk.embeddings = embedding

        batch_size = 100
        for i in range(0, len(chunk_docs), batch_size):
            docs = [asdict(c) for c in chunk_docs[i:i + batch_size]]
            search_client.upload_documents(docs)

        total_chunks += len(chunk_docs)
        logger.info(f"  Uploaded {len(chunk_docs)} chunks for {source['title']}")

    logger.info("Ingestion complete!")
    logger.info(f"Total chunks indexed: {total_chunks}")
    if failed_sources:
        logger.warning(f"Failed sources: {failed_sources}")
    logger.info("Index ready for Researcher agent queries")


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Ingest security KB into Azure AI Search")
    parser.add_argument("--sources", default="kb/sources.json", help="Path to sources.json")
    parser.add_argument("--config", default="kb/kb_config.json", help="Path to kb_config.json")
    parser.add_argument("--skip-existing", action="store_true", help="Skip re-ingesting existing chunks")
    
    args = parser.parse_args()
    
    ingest_sources(args.sources, args.config, args.skip_existing)
