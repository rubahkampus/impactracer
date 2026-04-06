"""
ImpacTracer Configuration Module
=================================

RESPONSIBILITY
    Centralizes every tunable parameter in the system into a single
    Pydantic BaseSettings object loaded from environment variables
    and a .env file. No other module may hardcode configuration values.
    All LLM temperature, seed, BFS depth limits, token budgets, and
    storage paths are defined here.

ARCHITECTURAL CONSTRAINTS
    1. temperature MUST be 0.0 and seed MUST be 42 (or configurable
       via env) to maximize reproducibility per NFR-07.
    2. bfs_single_hop_edges MUST list exactly IMPORTS,
       DEPENDS_ON_EXTERNAL, and RENDERS per Subbab III.2.4.3.
    3. All storage paths default to ./data/ for zero-managed-services
       local execution per NFR-03.

CONSUMED BY
    Every module in the project imports Settings and reads parameters
    from it. The CLI instantiates Settings once at startup and passes
    it through the call chain.
"""
from __future__ import annotations

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # LLM — Google Gemini (primary); openai_api_key kept for future reference
    google_api_key: str = ""
    openai_api_key: str = ""
    llm_model: str = "gemini-2.5-flash"
    llm_temperature: float = 0.0
    llm_seed: int = 42  # informational; Gemini uses temperature=0 for determinism

    # Embedding and Reranking
    embedding_model: str = "BAAI/bge-m3"
    reranker_model: str = "BAAI/bge-reranker-v2-m3"
    embedding_batch_size: int = 32
    embedding_max_length: int = 512

    # Storage
    db_path: str = "./data/impactracer.db"
    chroma_path: str = "./data/chroma_store"

    # Indexer
    top_k_traceability: int = 5

    # Retrieval
    max_candidates_per_query: int = 15
    max_candidates_post_rrf: int = 15
    max_candidates_post_rerank: int = 15
    rrf_k: int = 60

    # BFS
    bfs_global_max_depth: int = 3
    bfs_single_hop_edges: list[str] = [
        "IMPORTS",
        "DEPENDS_ON_EXTERNAL",
        "RENDERS",
    ]

    # Context Window
    llm_max_context_tokens: int = 100_000
    synthesis_system_prompt_tokens: int = 800
    tokens_per_cis_node: int = 250

    # Evaluation
    eval_k_values: list[int | str] = [5, 10, "all"]
    alpha: float = 0.05

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"
