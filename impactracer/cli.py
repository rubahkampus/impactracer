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
    """Build the knowledge stores from repository artifacts."""
    typer.echo(f"Indexing repository at {repo_path}...")
    # TODO: Wire to indexer pipeline
    typer.echo("Indexing complete.")


@app.command()
def analyze(
    cr_text: str = typer.Argument(..., help="Change Request text in natural language."),
    output: str = typer.Option("./outputs/impact_report.json", "-o", "--output"),
) -> None:
    """Analyze a Change Request against the indexed repository."""
    typer.echo("Running impact analysis...")
    # TODO: Wire to pipeline.runner.run_analysis()
    typer.echo(f"Report written to {output}")


if __name__ == "__main__":
    app()
