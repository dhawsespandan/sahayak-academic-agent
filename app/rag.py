"""Retrieval over the regulation PDFs (Qdrant in local mode + fastembed).

Ingestion: PDF -> text per page -> overlapping chunks -> bge-small embeddings
-> Qdrant collection. Each chunk keeps its document title and page number so
answers can cite their source.
"""
from functools import lru_cache

from fastembed import TextEmbedding
from pypdf import PdfReader
from qdrant_client import QdrantClient, models

from app import config

CHUNK_CHARS = 700
OVERLAP_CHARS = 150


@lru_cache(maxsize=1)
def embedder() -> TextEmbedding:
    return TextEmbedding(config.EMBED_MODEL, cache_dir=str(config.MODEL_CACHE))


@lru_cache(maxsize=1)
def client() -> QdrantClient:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    return QdrantClient(path=str(config.QDRANT_PATH))


def chunk_text(text: str, size: int = CHUNK_CHARS, overlap: int = OVERLAP_CHARS) -> list[str]:
    """Splits on paragraph boundaries where possible, with character overlap."""
    text = " ".join(text.split())
    chunks, start = [], 0
    while start < len(text):
        end = min(len(text), start + size)
        if end < len(text):
            # back off to the end of a sentence so a rule is not cut in half
            cut = text.rfind(". ", start + size // 2, end)
            if cut != -1:
                end = cut + 1
        chunks.append(text[start:end].strip())
        if end == len(text):
            break
        start = max(end - overlap, start + 1)
    return chunks


def load_pdf_chunks() -> list[dict]:
    items = []
    for pdf in sorted(config.DOCS_DIR.glob("*.pdf")):
        reader = PdfReader(pdf)
        title = (reader.pages[0].extract_text() or "").split("\n")[0].strip()
        for page_no, page in enumerate(reader.pages, start=1):
            for chunk in chunk_text(page.extract_text() or ""):
                items.append({"source": pdf.name, "title": title, "page": page_no, "text": chunk})
    return items


def build_index(force: bool = False) -> int:
    qc = client()
    if qc.collection_exists(config.COLLECTION):
        if not force:
            return qc.count(config.COLLECTION).count
        qc.delete_collection(config.COLLECTION)
    chunks = load_pdf_chunks()
    vectors = list(embedder().embed([c["text"] for c in chunks]))
    qc.create_collection(
        config.COLLECTION,
        vectors_config=models.VectorParams(size=len(vectors[0]), distance=models.Distance.COSINE),
    )
    qc.upsert(config.COLLECTION, points=[
        models.PointStruct(id=i, vector=v.tolist(), payload=c)
        for i, (c, v) in enumerate(zip(chunks, vectors))
    ])
    return len(chunks)


def search(query: str, k: int = config.RAG_TOP_K) -> list[dict]:
    vec = next(iter(embedder().query_embed(query))).tolist()
    hits = client().query_points(config.COLLECTION, query=vec, limit=k).points
    return [{**h.payload, "score": round(h.score, 3)} for h in hits]


def format_context(hits: list[dict]) -> str:
    return "\n\n".join(
        f"[{i}] ({h['title']}, page {h['page']})\n{h['text']}" for i, h in enumerate(hits, start=1)
    )
