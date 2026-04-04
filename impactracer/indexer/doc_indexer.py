"""
Document Indexer — Markdown Chunking and Classification
========================================================

RESPONSIBILITY
    Scans a directory for .md files, splits each into chunks at H2/H3
    heading boundaries, classifies each chunk (FR, NFR, Design, General),
    and returns structured chunk dicts ready for embedding and ChromaDB
    insertion.

INPUTS
    Directory path containing Markdown files (SRS, SDD).

OUTPUTS
    List of dicts, each with keys: chunk_id, source_file, section_title,
    chunk_type, text.

CHUNKING RULES (per Subbab III.2.1.1)
    1. Split on every H2 (##) and H3 (###) heading.
    2. Text between two consecutive headings forms one chunk.
    3. chunk_id = "{filename}::{slugified_section_title}"
    4. Deterministic: identical input always produces identical chunks.

CLASSIFICATION RULES (per Blueprint v3 Section 6.1)
    FR keywords:   "kebutuhan fungsional", "functional requirement", "use case"
    NFR keywords:  "non-fungsional", "non-functional", "kebutuhan non"
    Design keywords: "perancangan", "desain", "arsitektur", "design", "architecture"
    Default:       "General"

ARCHITECTURAL CONSTRAINTS
    Zero LLM calls. Purely deterministic string operations.
    Must use mistune for Markdown AST parsing to avoid regex fragility.
"""
from __future__ import annotations

# TODO: Implement chunk_markdown() and classify_chunk()
