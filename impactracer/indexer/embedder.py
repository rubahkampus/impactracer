"""
Embedding Module — BGE-M3 Cross-Lingual Dense Vectors
======================================================

RESPONSIBILITY
    Wraps the BAAI/bge-m3 model (or empirically selected alternative)
    to produce dense embedding vectors for document chunks and code
    units. Vectors are used for ChromaDB insertion and cosine similarity
    precomputation.

INPUTS
    List of text strings (chunk texts or embed_text from code nodes).

OUTPUTS
    numpy ndarray of shape (N, D) with D=1024 for bge-m3.

ARCHITECTURAL CONSTRAINTS
    1. Model runs locally via FlagEmbedding. No API calls.
    2. use_fp16=True for memory efficiency.
    3. max_length=512 tokens per input.
    4. return_dense=True, return_sparse=False, return_colbert_vecs=False.
    5. Zero LLM calls. Deterministic for identical inputs on identical
       hardware.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from FlagEmbedding import BGEM3FlagModel
from huggingface_hub import snapshot_download


def ensure_model_cached(model_name: str) -> None:
    """Download the model on first run if not already present in the HF cache.

    Checks for the model directory under ~/.cache/huggingface/hub before
    attempting any network access, so repeated runs pay zero overhead.
    """
    cache_dir = Path.home() / ".cache" / "huggingface" / "hub"
    model_dir = cache_dir / f"models--{model_name.replace('/', '--')}"
    if not model_dir.exists():
        print(f"[ImpacTracer] Downloading {model_name} (~570MB one-time)...")
        snapshot_download(model_name)


class Embedder:
    """Local BGE-M3 dense embedding model.

    Instantiate once and reuse; the model is loaded into memory on __init__.
    Call ensure_model_cached(model_name) before constructing if you want a
    friendly progress message on the first download.
    """

    def __init__(self, model_name: str = "BAAI/bge-m3") -> None:
        self.model = BGEM3FlagModel(model_name, use_fp16=True)

    def embed_batch(self, texts: list[str]) -> np.ndarray:
        """Embed a list of texts. Returns (N, D) float32 ndarray; D=1024 for bge-m3."""
        output = self.model.encode(
            texts,
            batch_size=32,
            max_length=512,
            return_dense=True,
            return_sparse=False,
            return_colbert_vecs=False,
        )
        return output["dense_vecs"]  # ndarray, not list

    def embed_single(self, text: str) -> list[float]:
        """Embed a single text string. Returns a plain Python list of floats."""
        return self.embed_batch([text])[0].tolist()
