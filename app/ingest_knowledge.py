"""Idempotently ingest the curated Day 5-B technical knowledge set."""

from pathlib import Path

from app.db.session import SessionLocal
from app.tools.knowledge import KnowledgeIngestor, load_knowledge_directory


def main() -> None:
    root = Path(__file__).resolve().parents[1] / "data" / "knowledge"
    documents = load_knowledge_directory(root)
    with SessionLocal.begin() as session:
        document_count, chunk_count = KnowledgeIngestor().ingest(session, documents)
    print(f"Ingested {document_count} documents and {chunk_count} chunks.")


if __name__ == "__main__":
    main()
