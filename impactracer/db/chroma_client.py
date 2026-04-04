"""
ChromaDB Client — File-Backed Vector Store
============================================

RESPONSIBILITY
    PersistentClient initialization and collection factory for
    doc_chunks and code_units. Both collections enforce cosine space.

ARCHITECTURAL CONSTRAINTS
    COLLECTION_CONFIG metadata MUST set hnsw:space to cosine.
    Default L2 produces incorrect ranking for normalized vectors.
    Zero server processes. Zero LLM calls.
"""
from __future__ import annotations
from pathlib import Path
import chromadb

COLLECTION_CONFIG = {"hnsw:space": "cosine"}

def get_chroma_client(chroma_path: str) -> chromadb.ClientAPI:
    Path(chroma_path).mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(path=chroma_path)

def init_collections(client: chromadb.ClientAPI):
    doc_col = client.get_or_create_collection(name="doc_chunks", metadata=COLLECTION_CONFIG)
    code_col = client.get_or_create_collection(name="code_units", metadata=COLLECTION_CONFIG)
    return doc_col, code_col
