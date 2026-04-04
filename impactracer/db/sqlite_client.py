"""
SQLite Client — Relational Store for Code Graph and Traceability Cache
======================================================================

RESPONSIBILITY
    Connection factory, schema initialization (DDL), and query helpers
    for impactracer.db. This module owns all four tables and is the
    ONLY module permitted to execute raw SQL.

TABLES
    code_nodes          One row per AST-extracted code entity.
    structural_edges    One row per typed dependency (8 edge types).
                        Composite PK (source_id, target_id, edge_type).
                        Index on (target_id, edge_type) optimizes BFS
                        reverse traversal queries.
    doc_code_candidates Precomputed cosine similarity pairs for
                        back-traceability.
    index_metadata      Key-value pairs for indexing provenance.

ARCHITECTURAL CONSTRAINTS
    WAL journal mode and NORMAL synchronous for write throughput.
    foreign_keys ON for referential integrity.
    Zero LLM calls. Entirely deterministic.
"""
from __future__ import annotations
import sqlite3
from pathlib import Path

SCHEMA_SQL = """
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS code_nodes (
    node_id             TEXT PRIMARY KEY,
    node_type           TEXT NOT NULL CHECK(node_type IN (
        'File','Class','Function','Method',
        'Interface','TypeAlias','Enum','ExternalPackage')),
    name                TEXT NOT NULL,
    file_path           TEXT,
    line_start          INTEGER,
    line_end            INTEGER,
    signature           TEXT,
    docstring           TEXT,
    source_code         TEXT,
    route_path          TEXT,
    file_classification TEXT CHECK(file_classification IN (
        'API_ROUTE','PAGE_COMPONENT','UI_COMPONENT',
        'UTILITY','TYPE_DEFINITION', NULL)),
    exported            INTEGER DEFAULT 0,
    embed_text          TEXT
);

CREATE TABLE IF NOT EXISTS structural_edges (
    source_id   TEXT NOT NULL,
    target_id   TEXT NOT NULL,
    edge_type   TEXT NOT NULL CHECK(edge_type IN (
        'CALLS','INHERITS','IMPLEMENTS','TYPED_BY',
        'DEFINES_METHOD','IMPORTS','DEPENDS_ON_EXTERNAL','RENDERS')),
    PRIMARY KEY (source_id, target_id, edge_type),
    FOREIGN KEY (source_id) REFERENCES code_nodes(node_id),
    FOREIGN KEY (target_id) REFERENCES code_nodes(node_id)
);
CREATE INDEX IF NOT EXISTS idx_edges_target ON structural_edges(target_id, edge_type);
CREATE INDEX IF NOT EXISTS idx_edges_source ON structural_edges(source_id, edge_type);

CREATE TABLE IF NOT EXISTS doc_code_candidates (
    code_id     TEXT NOT NULL,
    doc_id      TEXT NOT NULL,
    similarity  REAL NOT NULL,
    PRIMARY KEY (code_id, doc_id)
);
CREATE INDEX IF NOT EXISTS idx_dcc_code ON doc_code_candidates(code_id, similarity DESC);
CREATE INDEX IF NOT EXISTS idx_dcc_doc  ON doc_code_candidates(doc_id, similarity DESC);

CREATE TABLE IF NOT EXISTS index_metadata (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

def get_connection(db_path: str) -> sqlite3.Connection:
    """Open or create the database and ensure schema exists."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA_SQL)
    return conn
