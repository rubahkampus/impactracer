"""
ImpacTracer CLI — Command-Line Interface
==========================================

RESPONSIBILITY
    Provides two primary subcommands via the Typer framework.

    index   — Runs the offline indexing pipeline on a repository.
              Scans Markdown docs and TypeScript/TSX code, builds
              ChromaDB vectors, SQLite graph, and traceability cache.
              Zero LLM calls.

    analyze — Runs the online analysis pipeline on a single CR.
              Requires a previously indexed repository.
              Exactly 3 LLM calls (or 1 if GIGO rejects the CR).
              Outputs ImpactReport JSON to the specified path.

USAGE
    python -m impactracer index ./path/to/repo
    python -m impactracer analyze "Ubah aturan diskon VIP..." -o report.json

ARCHITECTURAL CONSTRAINTS
    1. CLI is a thin entry point. All logic resides in indexer/ and
       pipeline/ modules.
    2. Settings loaded once from .env at startup.
    3. Output directory created automatically if it does not exist.
"""
from __future__ import annotations

import datetime
import time
from pathlib import Path

import typer

app = typer.Typer(
    name="impactracer",
    help="Lean Dual-Store Change Impact Analysis Tool",
    no_args_is_help=True,
)


@app.command()
def index(
    repo_path: str = typer.Argument(..., help="Path to the repository root."),
) -> None:
    """Build the knowledge stores from repository artifacts.

    Pipeline (zero LLM calls):
      1. Init SQLite + ChromaDB stores (clear existing data for determinism).
      2. S2: Chunk all .md files under <repo>/docs/ into typed sections.
      3. S3: Parse all .ts/.tsx files; populate code_nodes + structural_edges.
      4. S4: Embed doc chunks + code units via BGE-M3; upsert into ChromaDB.
      5. S4: Compute doc↔code cosine similarity; store top-K in SQLite.
      6. Write index_metadata provenance record.
    """
    from impactracer.config import Settings
    from impactracer.db.chroma_client import get_chroma_client, init_collections
    from impactracer.db.sqlite_client import get_connection
    from impactracer.indexer.code_indexer import index_repository
    from impactracer.indexer.doc_indexer import index_docs
    from impactracer.indexer.embedder import Embedder, ensure_model_cached
    from impactracer.indexer.traceability import (
        compute_doc_code_candidates,
        store_doc_code_candidates,
    )

    t_start = time.perf_counter()

    repo = Path(repo_path).resolve()
    if not repo.is_dir():
        typer.echo(f"Error: '{repo}' is not a directory.", err=True)
        raise typer.Exit(1)

    settings = Settings()

    typer.echo(f"[ImpacTracer] Repository : {repo}")
    typer.echo(f"[ImpacTracer] DB path    : {settings.db_path}")
    typer.echo(f"[ImpacTracer] Chroma path: {settings.chroma_path}")

    # ── [1/6] Init stores ─────────────────────────────────────────────────
    typer.echo("\n[1/6] Initializing data stores...")
    conn = get_connection(settings.db_path)

    # Clear all tables so a re-run produces a bit-identical result.
    conn.executescript("""
        DELETE FROM doc_code_candidates;
        DELETE FROM structural_edges;
        DELETE FROM code_nodes;
        DELETE FROM index_metadata;
    """)
    conn.commit()

    chroma = get_chroma_client(settings.chroma_path)
    # Delete and recreate collections to clear stale vectors on re-run.
    for col_name in ("doc_chunks", "code_units"):
        try:
            chroma.delete_collection(col_name)
        except Exception:
            pass
    doc_col, code_col = init_collections(chroma)

    typer.echo("       Stores ready.")

    # ── [2/6] S2 — Doc chunking ───────────────────────────────────────────
    typer.echo("[2/6] Chunking Markdown documentation...")

    docs_dir = repo / "docs"
    if not docs_dir.is_dir():
        # Fallback: treat repo root as docs directory
        docs_dir = repo
        typer.echo(f"       No docs/ subdir found — scanning {repo.name}/ for .md files.")

    chunks: list[dict] = index_docs(str(docs_dir))

    chunk_type_counts: dict[str, int] = {}
    for c in chunks:
        ct = c["chunk_type"]
        chunk_type_counts[ct] = chunk_type_counts.get(ct, 0) + 1

    typer.echo(f"       {len(chunks)} chunks — {chunk_type_counts}")

    # ── [3/6] S3 — Code indexing ──────────────────────────────────────────
    typer.echo("[3/6] Parsing TypeScript/TSX source tree...")
    t3 = time.perf_counter()

    index_repository(str(repo), conn)

    node_count: int = conn.execute("SELECT COUNT(*) FROM code_nodes").fetchone()[0]
    edge_count: int = conn.execute("SELECT COUNT(*) FROM structural_edges").fetchone()[0]
    node_type_rows = conn.execute(
        "SELECT node_type, COUNT(*) FROM code_nodes "
        "GROUP BY node_type ORDER BY COUNT(*) DESC"
    ).fetchall()
    edge_type_rows = conn.execute(
        "SELECT edge_type, COUNT(*) FROM structural_edges "
        "GROUP BY edge_type ORDER BY COUNT(*) DESC"
    ).fetchall()

    typer.echo(f"       {node_count} nodes, {edge_count} edges  ({time.perf_counter()-t3:.1f}s)")
    typer.echo(f"       Nodes: { {r[0]: r[1] for r in node_type_rows} }")
    typer.echo(f"       Edges: { {r[0]: r[1] for r in edge_type_rows} }")

    # ── [4/6] S4 — Load embedding model ──────────────────────────────────
    typer.echo("[4/6] Loading BGE-M3 embedding model...")
    t4 = time.perf_counter()
    ensure_model_cached(settings.embedding_model)
    embedder = Embedder(settings.embedding_model)
    typer.echo(f"       Model ready  ({time.perf_counter()-t4:.1f}s)")

    # ── [5/6] S4 — Embed + insert into ChromaDB ───────────────────────────
    typer.echo("[5/6] Embedding and inserting vectors into ChromaDB...")

    # --- Doc chunks ---
    doc_ids: list[str] = []
    doc_vecs_arr = None

    if chunks:
        doc_texts = [c["text"] for c in chunks]
        doc_ids = [c["chunk_id"] for c in chunks]
        doc_metas = [
            {
                "source_file":   c["source_file"],
                "section_title": c["section_title"],
                "chunk_type":    c["chunk_type"],
            }
            for c in chunks
        ]
        t_emb = time.perf_counter()
        doc_vecs_arr = embedder.embed_batch(doc_texts)   # (N_doc, D)
        doc_col.upsert(
            ids=doc_ids,
            embeddings=doc_vecs_arr.tolist(),
            metadatas=doc_metas,
            documents=doc_texts,
        )
        typer.echo(
            f"       {len(chunks)} doc vectors -> doc_chunks  ({time.perf_counter()-t_emb:.1f}s)"
        )

    # --- Code units ---
    code_embed_ids: list[str] = []
    code_vecs_arr = None

    # Fix H: Exclude degenerate nodes whose embed_text is too short to produce
    # a meaningful BGE-M3 vector.  Short TypeAlias nodes (e.g. `type ID = string`,
    # 18 chars) generate generic vectors that pollute both ChromaDB retrieval and
    # the traceability similarity matrix.  These nodes remain in SQLite so that
    # TYPED_BY / graph edges are preserved; they are simply not embedded or
    # included in traceability candidates.
    _DEGENERATE_EMBED_MIN_LEN = 50

    code_rows = conn.execute(
        "SELECT node_id, embed_text, node_type, name, file_path, "
        "       file_classification, exported "
        "FROM code_nodes "
        "WHERE embed_text IS NOT NULL AND embed_text != '' "
        "AND length(embed_text) >= ?",
        (_DEGENERATE_EMBED_MIN_LEN,),
    ).fetchall()

    if code_rows:
        code_embed_ids   = [r[0] for r in code_rows]
        code_embed_texts = [r[1] for r in code_rows]
        code_metas = [
            {
                "node_type":          r[2],
                "name":               r[3],
                "file_path":          r[4] or "",
                "file_classification": r[5] or "",
                "exported":           bool(r[6]),
            }
            for r in code_rows
        ]
        t_emb = time.perf_counter()
        code_vecs_arr = embedder.embed_batch(code_embed_texts)  # (N_code, D)
        code_col.upsert(
            ids=code_embed_ids,
            embeddings=code_vecs_arr.tolist(),
            metadatas=code_metas,
            documents=code_embed_texts,
        )
        typer.echo(
            f"       {len(code_rows)} code vectors -> code_units  ({time.perf_counter()-t_emb:.1f}s)"
        )

    # ── [6/6] S4 — Traceability precomputation ────────────────────────────
    typer.echo("[6/6] Computing doc<->code traceability candidates...")
    traceability_count = 0

    if doc_vecs_arr is not None and code_vecs_arr is not None:
        import numpy as np
        doc_vecs_dict  = {doc_ids[i]:          doc_vecs_arr[i]  for i in range(len(doc_ids))}
        code_vecs_dict = {code_embed_ids[i]: code_vecs_arr[i] for i in range(len(code_embed_ids))}

        t_tr = time.perf_counter()
        candidates = compute_doc_code_candidates(
            code_vecs_dict,
            doc_vecs_dict,
            top_k=settings.top_k_traceability,
            min_similarity=settings.min_traceability_similarity,  # Fix G
        )
        store_doc_code_candidates(conn, candidates)
        traceability_count = len(candidates)
        typer.echo(
            f"       {traceability_count} pairs stored  ({time.perf_counter()-t_tr:.1f}s)"
        )

    # ── Metadata ──────────────────────────────────────────────────────────
    conn.executemany(
        "INSERT OR REPLACE INTO index_metadata (key, value) VALUES (?, ?)",
        [
            ("repo_path",          str(repo)),
            ("last_indexed_at",    datetime.datetime.now().isoformat()),
            ("embedding_model",    settings.embedding_model),
            ("top_k_traceability", str(settings.top_k_traceability)),
            ("total_code_nodes",   str(node_count)),
            ("total_doc_chunks",   str(len(chunks))),
        ],
    )
    conn.commit()
    conn.close()

    elapsed = time.perf_counter() - t_start

    typer.echo(f"\n{'=' * 60}")
    typer.echo("INDEXING COMPLETE")
    typer.echo(f"{'=' * 60}")
    typer.echo(f"  Doc chunks       : {len(chunks)}")
    typer.echo(f"  Code nodes       : {node_count}")
    typer.echo(f"  Structural edges : {edge_count}")
    typer.echo(f"  Code vectors     : {len(code_rows)}")
    typer.echo(f"  Traceability     : {traceability_count} pairs")
    typer.echo(f"  Elapsed          : {elapsed:.1f}s")
    typer.echo(f"  DB               : {settings.db_path}")
    typer.echo(f"  Chroma store     : {settings.chroma_path}")
    typer.echo(f"{'=' * 60}")


@app.command()
def analyze(
    cr_text: str = typer.Argument(..., help="Change Request text in natural language."),
    output: str = typer.Option("./outputs/impact_report.json", "-o", "--output"),
) -> None:
    """Analyze a Change Request and produce a structured Impact Report.

    Pipeline (exactly 3 LLM calls for an actionable CR):
      1. LLM Call #1 — Interpret CR + GIGO validation.
      2. Hybrid search + RRF + BGE-Reranker (zero LLM).
      3. LLM Call #2 — Validate SIS candidates.
      4. Seed resolution + BFS propagation (zero LLM).
      5. LLM Call #3 — Synthesize ImpactReport JSON.
    """
    import json

    from impactracer.config import Settings
    from impactracer.pipeline.runner import run_analysis

    t_start = time.perf_counter()
    settings = Settings()

    typer.echo("[ImpacTracer] Starting analysis...")
    typer.echo(f"[ImpacTracer] CR: {cr_text[:120]}{'...' if len(cr_text) > 120 else ''}")

    try:
        report = run_analysis(cr_text, settings)
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1)

    # Write JSON output
    out_path = Path(output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        report.model_dump_json(indent=2),
        encoding="utf-8",
    )

    elapsed = time.perf_counter() - t_start

    typer.echo(f"\n{'=' * 60}")
    typer.echo("ANALYSIS COMPLETE")
    typer.echo(f"{'=' * 60}")
    typer.echo(f"  Scope          : {report.estimated_change_scope}")
    typer.echo(f"  Impacted items : {len(report.impacted_items)}")
    typer.echo(f"  Conflicts      : {len(report.requirement_conflicts)}")
    typer.echo(f"  Elapsed        : {elapsed:.1f}s")
    typer.echo(f"  Report         : {out_path}")
    typer.echo(f"{'=' * 60}")


if __name__ == "__main__":
    app()
