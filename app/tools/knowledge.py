"""Document ingestion and citation-preserving technical knowledge retrieval."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import Field
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.contracts.common import EvidenceReference
from app.contracts.tool import ToolInput, ToolOutput
from app.db import models
from app.services.embeddings import EmbeddingProvider, HashEmbeddingProvider, cosine_similarity


@dataclass(frozen=True)
class KnowledgeDocument:
    title: str
    source_url: str
    license: str
    content: str
    metadata: dict[str, Any]


def chunk_text(text: str, *, max_chars: int = 1200, overlap_chars: int = 150) -> list[str]:
    if max_chars < 200 or not 0 <= overlap_chars < max_chars:
        raise ValueError("Invalid chunk configuration")
    paragraphs = [item.strip() for item in text.split("\n\n") if item.strip()]
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        candidate = f"{current}\n\n{paragraph}".strip()
        if current and len(candidate) > max_chars:
            chunks.append(current)
            current = f"{current[-overlap_chars:]}\n\n{paragraph}".strip()
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


class KnowledgeIngestor:
    def __init__(self, embedding_provider: EmbeddingProvider | None = None) -> None:
        self.embedding_provider = embedding_provider or HashEmbeddingProvider()

    def ingest(self, session: Session, documents: list[KnowledgeDocument]) -> tuple[int, int]:
        document_count = 0
        chunk_count = 0
        for source in documents:
            if not source.title.strip() or not source.source_url.startswith("https://"):
                raise ValueError("Knowledge documents require a title and HTTPS source URL")
            if not source.license.strip():
                raise ValueError("Knowledge documents require license metadata")
            document = session.scalar(
                select(models.Document).where(models.Document.source_url == source.source_url)
            )
            metadata = {**source.metadata, "ingested_at": datetime.now(UTC).isoformat()}
            if document is None:
                document = models.Document(
                    title=source.title,
                    source_url=source.source_url,
                    license=source.license,
                    metadata_json=metadata,
                )
                session.add(document)
                session.flush()
            else:
                document.title = source.title
                document.license = source.license
                document.metadata_json = metadata
                session.execute(
                    delete(models.DocumentChunk).where(
                        models.DocumentChunk.document_id == document.id
                    )
                )
            chunks = chunk_text(source.content)
            for index, content in enumerate(chunks, start=1):
                session.add(
                    models.DocumentChunk(
                        document_id=document.id,
                        content=content,
                        embedding=self.embedding_provider.embed(content),
                        page_or_section=f"chunk-{index}",
                    )
                )
            document_count += 1
            chunk_count += len(chunks)
        session.flush()
        return document_count, chunk_count


class KnowledgeSearchInput(ToolInput):
    query: str = Field(min_length=3, max_length=1000)
    top_k: int = Field(default=4, ge=1, le=10)
    minimum_relevance: float = Field(default=0.05, ge=-1, le=1)


class KnowledgeCitation(ToolOutput):
    document_id: str
    chunk_id: str
    title: str
    source_url: str
    license: str
    section: str | None
    excerpt: str
    relevance: float = Field(ge=-1, le=1)
    evidence: list[EvidenceReference] = Field(default_factory=list, exclude=True)
    warnings: list[str] = Field(default_factory=list, exclude=True)


class KnowledgeSearchOutput(ToolOutput):
    status: str
    query: str
    citations: list[KnowledgeCitation] = Field(default_factory=list)


class KnowledgeSearchTool:
    def __init__(self, embedding_provider: EmbeddingProvider | None = None) -> None:
        self.embedding_provider = embedding_provider or HashEmbeddingProvider()

    def __call__(self, data: KnowledgeSearchInput, session: Session) -> KnowledgeSearchOutput:
        query_vector = self.embedding_provider.embed(data.query)
        rows = session.execute(
            select(models.DocumentChunk, models.Document).join(
                models.Document, models.Document.id == models.DocumentChunk.document_id
            )
        ).all()
        ranked = []
        for chunk, document in rows:
            if chunk.embedding is None:
                continue
            relevance = cosine_similarity(query_vector, list(chunk.embedding))
            if relevance >= data.minimum_relevance:
                ranked.append((relevance, chunk, document))
        ranked.sort(key=lambda item: (-item[0], str(item[1].id)))
        citations = [
            KnowledgeCitation(
                document_id=str(document.id),
                chunk_id=str(chunk.id),
                title=document.title,
                source_url=document.source_url or "",
                license=document.license or "unspecified",
                section=chunk.page_or_section,
                excerpt=chunk.content[:500],
                relevance=round(relevance, 6),
            )
            for relevance, chunk, document in ranked[: data.top_k]
        ]
        if not citations:
            return KnowledgeSearchOutput(
                status="no_results",
                query=data.query,
                warnings=["No sufficiently relevant stored technical source was found."],
            )
        evidence = [
            EvidenceReference(
                evidence_id=item.chunk_id,
                source=item.source_url,
                kind="technical_document",
                reliability=0.85,
                metadata={
                    "document_id": item.document_id,
                    "title": item.title,
                    "license": item.license,
                    "section": item.section,
                    "relevance": item.relevance,
                },
            )
            for item in citations
        ]
        return KnowledgeSearchOutput(
            status="answered", query=data.query, citations=citations, evidence=evidence
        )


def load_knowledge_directory(path: Path) -> list[KnowledgeDocument]:
    import json

    manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
    return [
        KnowledgeDocument(
            title=item["title"],
            source_url=item["source_url"],
            license=item["license"],
            content=(path / item["file"]).read_text(encoding="utf-8"),
            metadata={"publisher": item["publisher"], "content_kind": "curated_summary"},
        )
        for item in manifest["documents"]
    ]
