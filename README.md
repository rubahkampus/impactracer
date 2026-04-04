# ImpacTracer

**Lean Dual-Store Change Impact Analysis Tool**

ImpacTracer is a CLI-based tool that predicts source code elements impacted by a natural language Change Request (CR) before implementation begins. It combines a vector store for semantic search with a relational store for deterministic structural propagation via BFS on an AST-derived dependency graph.

## Architecture Overview

The system operates in two phases.

**Offline Indexing** processes Markdown documentation (SRS/SDD) and TypeScript/TSX source code to build two persistent knowledge stores. The vector store (ChromaDB, file-backed, cosine space) holds dense embeddings for semantic search. The relational store (SQLite) holds AST-extracted code nodes, typed dependency edges, and precomputed traceability candidates. Zero LLM calls occur during indexing. All outputs are deterministic.

**Online Analysis** receives a CR in natural language and executes a nine-step pipeline with exactly three LLM calls. Step 1 interprets the CR and validates its feasibility (GIGO check). Steps 2 through 5 perform hybrid search (dense + BM25), RRF fusion, cross-encoder reranking, and LLM-based contextual validation to form the Starting Impact Set (SIS). Steps 6 through 8 resolve document nodes to code nodes, propagate impact via deterministic BFS on the dependency graph, and assemble a token-budgeted context. Step 9 synthesizes the final ImpactReport via the third LLM call with enforced JSON schema.

## Prerequisites

1. Python 3.11 or newer.
2. Approximately 2 GB of disk space for the BGE-M3 embedding model and BGE-Reranker-v2-M3 cross-encoder model (downloaded automatically on first run).
3. An API key for an OpenAI-compatible LLM provider (OpenRouter, Google AI Studio, or direct OpenAI).
4. A repository containing Markdown documentation and TypeScript/TSX source code for analysis.

## Setup Instructions

### Step 1. Clone the Repository

```bash
git clone https://github.com/your-username/impactracer.git
cd impactracer
```

### Step 2. Create and Activate a Virtual Environment

```bash
python -m venv .venv
source .venv/bin/activate    # Linux/macOS
# .venv\Scripts\activate     # Windows
```

### Step 3. Install Dependencies

```bash
pip install -r requirements.txt
```

### Step 4. Configure Environment Variables

```bash
cp .env.example .env
```

Open the `.env` file and replace `sk-your-key-here` with your actual LLM API key. If you are using OpenRouter, keep the OPENAI_BASE_URL as provided. If you are using direct OpenAI, change it to `https://api.openai.com/v1`.

### Step 5. Prepare Local Storage Directories

The data directories are created automatically on first run. If you want to create them manually, execute the following command.

```bash
mkdir -p data outputs
```

The `data/` directory will contain the SQLite database file (`impactracer.db`) and the ChromaDB persistent store directory (`chroma_store/`). The `outputs/` directory will contain generated ImpactReport JSON files.

### Step 6. Verify Installation

```bash
python -m impactracer --help
```

This command should display the two available subcommands (`index` and `analyze`).

## Usage

### Indexing a Repository

```bash
python -m impactracer index ./path/to/your/repo
```

This command scans all `.md` files for document chunks and all `.ts`/`.tsx` files for code nodes and dependency edges. On completion, it prints statistics (number of chunks, nodes, edges, and traceability candidates).

### Analyzing a Change Request

```bash
python -m impactracer analyze "Ubah aturan diskon bertingkat untuk pelanggan VIP agar batas minimum transaksi diturunkan dari sepuluh juta menjadi lima juta rupiah." -o outputs/report.json
```

This command executes the full nine-step analysis pipeline and writes the structured ImpactReport to the specified output path.

## Project Structure

```
impactracer/
├── __main__.py             Entry point for python -m impactracer
├── cli.py                  Typer CLI with index and analyze commands
├── config.py               Centralized settings from .env
├── models.py               All Pydantic schemas and dataclasses
├── db/
│   ├── sqlite_client.py    SQLite connection, schema DDL, query helpers
│   └── chroma_client.py    ChromaDB PersistentClient, collection factory
├── indexer/
│   ├── doc_indexer.py       Markdown chunking and classification
│   ├── code_indexer.py      Tree-sitter AST extraction (nodes + edges)
│   ├── embedder.py          BGE-M3 embedding wrapper
│   ├── reranker.py          BGE-Reranker cross-encoder wrapper
│   └── traceability.py      Cosine similarity precomputation
├── pipeline/
│   ├── interpreter.py       LLM Call 1 (CR interpretation + GIGO)
│   ├── retriever.py         Dual-path hybrid search + RRF
│   ├── validator.py         LLM Call 2 (SIS contextual validation)
│   ├── seed_resolver.py     Doc-chunk to code-node resolution
│   ├── graph_bfs.py         Deterministic BFS with edge config
│   ├── context_builder.py   Token-budgeted context assembly
│   ├── synthesizer.py       LLM Call 3 (ImpactReport synthesis)
│   └── runner.py            Full pipeline orchestrator
└── eval/
    ├── annotator_tool.py    CLI helper for AIS ground truth
    ├── metrics.py           P@K, R@K, F1@K, MRR computation
    └── ablation.py          Five-variant ablation study runner
```

## Evaluation

The evaluation framework supports five ablation variants (B0, B1, B2, S1, S2) executed via the `eval/ablation.py` module. All variants operate on the same indexed data. Statistical significance is assessed via Wilcoxon signed-rank test on paired F1@10 differences. Refer to the thesis Chapter III Section 7 for the full evaluation protocol.

## License

This project is developed as part of a Master's thesis at Institut Teknologi Bandung (ITB), 2026.
