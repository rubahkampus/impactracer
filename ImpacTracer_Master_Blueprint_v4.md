# ImpacTracer Master Blueprint v4.0
## From Naive Graph-RAG to Specialized Software Engineering Precision

**Version:** 4.0 (Design Specification) | **Status:** APPROVED FOR IMPLEMENTATION
**Predecessor:** ImpacTracer_Master_Blueprint_v3.md (v3.3 — Phase 3 Remediation Complete)
**Target Runtime:** Python 3.11+ | **Execution Model:** Local CLI, zero managed services
**Prepared for:** PhD Thesis Committee Review — Dr. Haidar, Lead Architect

---

## 0. Document Conventions

All references to line numbers, column names, and function signatures in this document
reflect the v3.3 codebase state as of commit `cc8c89b`. Where a v4.0 change modifies an
existing component, the superseded v3.3 behaviour is explicitly noted and the migration
path is described. No breaking changes to the public CLI interface are introduced.

**Change notation:**
- `[NEW]` — Net-new component, table, or edge type not present in v3.
- `[MOD]` — Modification to an existing v3 component.
- `[SCHEMA]` — Requires a SQLite schema migration (`ALTER TABLE` or new `CREATE TABLE`).
- `[OFFLINE]` — Affects only the offline indexing pipeline (`impactracer index`).
- `[ONLINE]` — Affects only the online analysis pipeline (`impactracer analyze`).
- `[BOTH]` — Affects both pipelines.

---

## 1. Executive Summary

### 1.1 The v3 Achievement Ceiling

ImpacTracer v3.3 delivered a functional, end-to-end Change Impact Analysis pipeline for
a Next.js/TypeScript codebase. It correctly traverses structural CALLS, IMPORTS, RENDERS,
and TYPED_BY relationships, fuses dense and lexical retrieval via Reciprocal Rank Fusion,
and validates candidate impact sets through a three-call LLM budget (CR Interpretation →
SIS Validation → Synthesis).

The v3.3 smoke test on the "Duplicate Commission Listing" CR confirms that the system
correctly identifies the service and repository layer impacts (`createListingFromForm`,
`createCommissionListing`). However, two structural failure modes persist that represent
the ceiling of what v3's architecture can achieve without fundamental re-engineering:

**Failure Mode 1 — File-Proximity Bias:**
`applySlotDelta` is confirmed as a false-positive SIS seed in every v3.3 run. The root
cause is not a retrieval failure (Fix E+F resolved that) nor a score threshold issue (Fix
C addressed it) nor missing context in the validator prompt (Fix B addressed it). The root
cause is that `applySlotDelta` is architecturally co-resident with the correct targets in
`commissionListing.service.ts`. When the SIS Validator receives multiple candidates from
the same file alongside genuine targets, it exhibits a **File-Proximity Bias**: functions
in a file named by the CR are over-confirmed relative to their individual functional
relevance. This is an LLM judgment limitation that cannot be resolved by prompt
engineering alone; it requires a pre-validator plausibility gate that enforces per-file
candidate density limits.

**Failure Mode 2 — UI-Layer Recall Miss:**
The `CommissionListingPage.tsx` component — the dashboard component that should receive
a "Duplicate" button — never appears in the CIS. The retrieval system finds the service
layer correctly, but the path from service → UI component does not exist in the v3 graph.
This is because: (a) Next.js data fetching passes data through SSR props (server-side
`getServerSideProps` or App Router `async page()`) rather than direct imports; (b) the
API calls connecting client components to service logic (`fetch('/api/commissions/...')`)
are invisible to our AST extractor; (c) JSX prop-callbacks (e.g., `onDuplicate={handler}`)
create semantic dependencies that our graph does not model.

### 1.2 The v4.0 Philosophy

ImpacTracer v4.0 moves from **generic Graph-RAG** (a knowledge graph + retrieval system
that happens to contain code) to **Specialized Software Engineering Graph-RAG** (a system
architected around the specific structural and semantic patterns of the Next.js/TypeScript
paradigm it analyzes).

The three design principles that distinguish v4.0 from v3:

1. **Graph Completeness over Retrieval Breadth:** Rather than relying on the LLM validator
   to distinguish true from false positives in a broad retrieval set, v4.0 invests in a
   richer, more precise graph — one that includes the implicit edges (API string matching,
   hook dependencies, prop callbacks, dynamic imports) that the current graph omits. A
   richer graph reduces the burden on the LLM validator and improves recall without
   increasing false positives.

2. **Pre-Validation Plausibility Gates:** The LLM validator (LLM Call #2) should receive
   only semantically plausible candidates. v4.0 introduces two deterministic gates before
   LLM Call #2: a cross-collection deduplication step (Step 3.6) and a file-density
   plausibility gate (Step 3.7). These gates require zero LLM calls — they are pure
   structural and heuristic filters that remove the over-represented false positives that
   cause the validator to hallucinate confirmations under file-proximity bias.

3. **Layer-Semantic Awareness:** Every node in the ImpacTracer graph has a
   `file_classification` (`UI_COMPONENT`, `API_ROUTE`, `PAGE_COMPONENT`, `UTILITY`,
   `TYPE_DEFINITION`). v4.0 exploits this metadata to apply layer-aware affinity scoring
   in both retrieval (Adaptive RRF) and traceability (Fix I Layer-Aware Affinity Filter),
   ensuring that a "duplicate listing" CR retrieves UI layer nodes with higher affinity
   and does not pollute the traceability matrix with cross-layer noise.

---

## 2. Updated Pipeline Flow

### 2.1 v3.3 Pipeline (Baseline)

```
[OFFLINE]
  Repo → tree-sitter AST → code_nodes + structural_edges (SQLite)
       → BGE-M3 embed → ChromaDB (code_collection)
       → Markdown chunk → BGE-M3 embed → ChromaDB (doc_collection)
       → compute_doc_code_candidates → doc_code_candidates (SQLite)

[ONLINE — impactracer analyze]
  S0: Load SQLite + ChromaDB + graph + BM25
  S1: LLM Call #1 — CR Interpretation (CRInterpretation)
      └─ GIGO Checkpoint (is_actionable guard)
  S2: Hybrid Search (BGE-M3 dense + BM25Okapi, dual-path) + RRF fusion
  S3: BGE-Reranker-v2-M3 cross-encoder scoring
  S3.5: Hard score filter (min_reranker_score_for_validation = 0.01)
  S4: LLM Call #2 — SIS Validation (CandidateVerdict CoT)
  S5: Seed Resolution (doc-chunk SIS → code seeds via doc_code_candidates)
      └─ Fix D: Confidence-Tiered BFS seed classification
  S6: BFS Propagation (EDGE_CONFIG, high_confidence_seeds)
  S7: Fetch backlinks + code snippets
  S8: Token-budgeted context assembly
  S9: LLM Call #3 — Synthesis (ImpactReport)
```

### 2.2 v4.0 Pipeline (Target)

New steps are marked `[NEW]`. Modified steps are marked `[MOD]`.

```
[OFFLINE — impactracer index]
  Repo → tree-sitter AST Pass 1 (node extraction)
       ├─ [MOD] Fix E+F: synthetic docstring + File-node enrichment (v3.3)
       ├─ [NEW] P4.B.1: Skeletonization pass → skeleton_snippet column
       └─ code_nodes (SQLite) ← [SCHEMA] + skeleton_snippet column

  AST Pass 2 (edge extraction)
       ├─ CALLS, IMPORTS, RENDERS, TYPED_BY, DEFINES_METHOD (v3)
       ├─ [NEW] P4.B.2: CLIENT_API_CALLS (fetch/axios string matching)
       ├─ [NEW] P4.C.1: HOOK_DEPENDS_ON (useEffect/useCallback dep arrays)
       ├─ [NEW] P4.C.1: PASSES_CALLBACK (JSX onXxx={fn} props)
       ├─ [NEW] P4.B.3: DYNAMIC_IMPORT (dynamic() / React.lazy() calls)
       └─ [NEW] P4.C.2: FIELDS_ACCESSED (member expression field tracking)
       → structural_edges (SQLite)

  Embedding
       → BGE-M3 embed(code_nodes.embed_text) → ChromaDB code_collection
       → BGE-M3 embed(doc_chunks) → ChromaDB doc_collection

  Traceability
       ├─ [NEW] P4.A.5: Fix I layer_compat() affinity scoring
       └─ [MOD] compute_doc_code_candidates(min_similarity, layer_compat)
          → doc_code_candidates (SQLite)

  Hashing [NEW — P4.A.4]
       └─ SHA-256(file_content) → file_hashes (SQLite) [SCHEMA]

[ONLINE — impactracer analyze]
  S0:  Load SQLite + ChromaDB + graph + BM25
  S1:  LLM Call #1 — CR Interpretation
       ├─ [MOD] P4.A.2: + named_entry_points field in CRInterpretation
       └─ GIGO Checkpoint
  S2:  [MOD] P4.A.3: Adaptive RRF (cr_type-weighted fusion)
  S3:  BGE-Reranker-v2-M3 cross-encoder scoring
  S3.5: Hard score filter (threshold=0.01, v3.3)
  S3.6: [NEW] P4.A.1: Cross-Collection Semantic Deduplication
  S3.7: [NEW] P4.A.2: Semantic Seed Arbitration (File-Density Plausibility Gate)
        + [NEW] P4.A.5: Fix I Layer-Aware Candidate Masking
  S4:  [MOD] LLM Call #2 — SIS Validation (uses skeleton_snippet)
  S5:  Seed Resolution
       └─ Fix D: Confidence-Tiered BFS seed classification (v3.3)
  S6:  [MOD] BFS Propagation (extended EDGE_CONFIG with new edge types)
  S7:  Fetch backlinks + code snippets
  S8:  Token-budgeted context assembly
       └─ [NEW] P4.D.1 (optional): skeleton_snippet fallback for overflowed nodes
  S9:  LLM Call #3 — Synthesis (ImpactReport)
```

### 2.3 Step-Level Interaction Diagram

```
               CRInterpretation
               ┌─────────────────┐
               │ primary_intent  │
               │ change_type     │◄── S1: LLM Call #1
               │ affected_layers │
               │ excluded_ops    │
               │ named_entry_pts │ ← [NEW v4.0]
               └────────┬────────┘
                        │
                        ▼
     S2: Adaptive RRF ──────────────────────────── S2 uses cr_type + affected_layers
          ↓
     S3: Cross-Encoder Reranker (BGE)
          ↓
     S3.5: Score Filter (threshold=0.01)
          ↓
     S3.6: [NEW] Deduplication Gate ──── merges (doc_chunk + code_node) for same operation
          ↓
     S3.7: [NEW] Plausibility Gate ──── file-density limit + named_entry_pts check
                + Fix I Affinity Mask ── layer_compat() scoring applied
          ↓
     S4: LLM Call #2 — Validator
          receives: skeleton_snippet [NEW], file_path, reranker_score,
                    excluded_ops, named_entry_pts [NEW]
          ↓
     S5: Seed Resolution + Fix D tier classification
          ↓
     S6: BFS with extended edge types (9 total vs 8 in v3)
          ↓
     S7–S9: Context Build → Synthesis
```

---

## 3. Phased Implementation Roadmap

The roadmap is partitioned into four phases ordered by engineering ROI (impact-per-hour
of implementation work). Each phase is independently deployable with no breaking changes
to the API surface or output schema.

---

### Phase A — Plausibility Gates & Retrieval Precision (Highest ROI)

*Addresses File-Proximity Bias and cross-layer noise. Zero new AST passes. All changes
are in the online pipeline (retriever.py, runner.py, validator.py) and the offline
traceability computation (traceability.py). Estimated implementation: 2–3 days.*

---

#### P4.A.1 — Cross-Collection Semantic Deduplication [Step 3.6]

**Rationale:** In v3.3, a single operation (e.g., "create commission listing") can appear
in the candidate list as both a doc-chunk node (an SRS requirement describing it) and a
code-node (the function `createListingFromForm` implementing it). These two candidates
represent the same conceptual operation and consume two of the validator's attention slots.
When both are confirmed, both become SIS seeds, and the doc-chunk seed resolves back to
`createListingFromForm` via `doc_code_candidates`, producing a duplicate BFS seed. This
wastes one of the five `bfs_high_conf_top_n` slots on a redundant path.

**Implementation:**
Insert Step 3.6 between S3.5 (score filter) and S3.7 (plausibility gate) in `runner.py`.

```python
# ── Step 3.6: Cross-Collection Semantic Deduplication ─────────────────────
# For each doc-chunk candidate, check if its top-1 code resolution
# (via doc_code_candidates) is already present as a code-node candidate.
# If yes: merge — keep the code-node, annotate it with the doc-chunk's
# doc_id for traceability. Drop the doc-chunk candidate.
# If no: retain the doc-chunk candidate (it will resolve in Step 5).

code_candidate_ids = {c["node_id"] for c in candidates if c.get("node_type") != "DocChunk"}
deduped = []
for cand in candidates:
    if cand.get("node_type") == "DocChunk":
        # Look up top-1 code resolution
        top_code = conn.execute(
            "SELECT code_id FROM doc_code_candidates WHERE doc_id=? ORDER BY similarity DESC LIMIT 1",
            (cand["node_id"],),
        ).fetchone()
        if top_code and top_code[0] in code_candidate_ids:
            # Merge: annotate the code candidate with this doc's backlink
            for cc in deduped:
                if cc["node_id"] == top_code[0]:
                    cc.setdefault("merged_doc_ids", []).append(cand["node_id"])
            continue  # drop the doc-chunk duplicate
    deduped.append(cand)
candidates = deduped
logger.info("Dedup (Step 3.6): {} → {} candidates", pre_dedup_count, len(candidates))
```

**Impact on BFS Budget:** With deduplication, the top-5 high-confidence BFS seeds are
5 *distinct* code operations rather than potentially 3 distinct operations with 2
doc-chunk duplicates. This is a lossless compression: no true-positive information is
discarded because the merged doc-chunk's traceability backlinks are preserved on the code
candidate.

---

#### P4.A.2 — Semantic Seed Arbitration: File-Density Plausibility Gate [Step 3.7]

**Rationale:** This is the primary fix for File-Proximity Bias. When `commissionListing
.service.ts` contributes 2+ candidates to LLM Call #2 alongside candidates from other
files, the LLM exhibits a systematic over-confirmation tendency: any function in a file
named by the CR is treated as implicitly relevant. The gate enforces that no single file
contributes more than one candidate per "file-density threshold" without a specific
functional justification anchored to `named_entry_points` from CR Interpretation.

**New `CRInterpretation` Field:**
```python
named_entry_points: list[str] = Field(
    default_factory=list,
    description=(
        "1-4 specific function or component name patterns that this CR "
        "explicitly describes creating, modifying, or invoking. These are "
        "the authoritative entry points of the change. "
        "Example: if the CR adds a duplicate listing button, "
        "named_entry_points = ['duplicateListing', 'DuplicateButton', "
        "'createListingFromTemplate']. "
        "Extract only names explicitly described, not inferred."
    ),
    max_length=4,
)
```

**Gate Logic (Step 3.7):**
```python
# ── Step 3.7: File-Density Plausibility Gate + Layer Affinity Mask ────────
from collections import Counter

FILE_DENSITY_THRESHOLD = 0.35  # A file may not contribute >35% of all candidates
MAX_PER_FILE = 2               # Hard cap: max candidates per file after gate

# 1. Compute file density
file_counts = Counter(c.get("file_path", "unknown") for c in candidates)
total = len(candidates)

# 2. For each over-represented file, keep only the top-N by reranker_score
# Unless a candidate's name matches a named_entry_point pattern
named_patterns = [p.lower() for p in getattr(cr_interp, "named_entry_points", [])]

gated: list[dict] = []
per_file_admitted: Counter = Counter()

for cand in sorted(candidates, key=lambda c: c.get("reranker_score", 0.0), reverse=True):
    fp = cand.get("file_path", "unknown")
    density = file_counts[fp] / total
    name_lower = cand.get("name", "").lower()

    # Always admit if name matches a named_entry_point
    is_named = any(pat in name_lower or name_lower in pat for pat in named_patterns)

    if is_named:
        gated.append(cand)
        per_file_admitted[fp] += 1
        continue

    # Admit up to MAX_PER_FILE for high-density files, unlimited for low-density
    if density > FILE_DENSITY_THRESHOLD:
        if per_file_admitted[fp] < MAX_PER_FILE:
            gated.append(cand)
            per_file_admitted[fp] += 1
        else:
            logger.debug(
                "Plausibility gate: dropped {} ({}) — file over-represented ({:.0%})",
                cand.get("name"), fp, density,
            )
    else:
        gated.append(cand)
        per_file_admitted[fp] += 1

candidates = gated
logger.info(
    "Plausibility gate (Step 3.7): {} → {} candidates (density threshold={:.0%})",
    total, len(candidates), FILE_DENSITY_THRESHOLD,
)
```

**Worked Example (v3.3 smoke test):**
Before gate: 4 candidates, 2 from `commissionListing.service.ts`
(`createListingFromForm` + `applySlotDelta`), 1 from `commissionListing.repository.ts`,
1 doc-chunk.
`commissionListing.service.ts` density = 2/4 = 50% > 35% threshold.
`named_entry_points` = `["duplicateListing", "createListingFromForm"]`.
`createListingFromForm` matches → always admitted.
`applySlotDelta` does not match → file at capacity (1 admission from named) → DROPPED.
After gate: 3 candidates (`createListingFromForm`, repository node, doc-chunk).
`applySlotDelta` never reaches LLM Call #2. File-Proximity Bias eliminated.

**Settings Exposure:**
```python
# config.py
plausibility_gate_density_threshold: float = 0.35
plausibility_gate_max_per_file: int = 2
```

---

#### P4.A.3 — Adaptive RRF Weighting [MOD Step 2]

**Rationale:** Standard RRF treats all four retrieval paths (dense-doc, BM25-doc,
dense-code, BM25-code) with equal contribution weight. For a `UI_CHANGE` or
`FEATURE_ADD` CR targeting a dashboard component, the code-layer dense path should
contribute more heavily than the BM25-doc path (which surface SRS sections describing
slot management that happen to mention "listing" and "komisi"). The formal derivation
appears in §6.1.

**Implementation:**
Extend `hybrid_search()` signature:
```python
def hybrid_search(
    queries: list[str],
    ...,
    cr_type: str = "FEATURE_ADD",       # from CRInterpretation.change_type
    affected_layers: list[str] = None,  # from CRInterpretation.affected_layers
    rrf_k: int = 60,
    max_candidates: int = 15,
) -> list[dict]:
```

Define the weight table in `config.py`:
```python
# Adaptive RRF path weights: keys are change_type, values are
# {path_label: weight} dicts. Applied as multipliers in the RRF denominator.
adaptive_rrf_weights: dict = {
    "FEATURE_ADD": {
        "dense_code": 1.5, "bm25_code": 1.2,
        "dense_doc":  0.8, "bm25_doc":  0.6,
    },
    "UI_CHANGE": {
        "dense_code": 2.0, "bm25_code": 1.0,
        "dense_doc":  0.5, "bm25_doc":  0.4,
    },
    "REFACTOR": {
        "dense_code": 1.8, "bm25_code": 1.5,
        "dense_doc":  0.4, "bm25_doc":  0.3,
    },
    "BUG_FIX": {
        "dense_code": 1.5, "bm25_code": 1.8,
        "dense_doc":  0.6, "bm25_doc":  0.8,
    },
    "REQUIREMENT_CHANGE": {
        "dense_code": 0.8, "bm25_code": 0.6,
        "dense_doc":  1.8, "bm25_doc":  2.0,
    },
    "_default": {
        "dense_code": 1.0, "bm25_code": 1.0,
        "dense_doc":  1.0, "bm25_doc":  1.0,
    },
}
```

RRF computation change:
```python
# Before (v3): uniform weight
rrf_score += 1.0 / (rrf_k + rank)

# After (v4): adaptive weight
w = weights.get(path_label, 1.0)
rrf_score += w / (rrf_k + rank)
```

The full mathematical derivation is provided in §6.1.

---

#### P4.A.4 — Incremental Indexing via File Content Hashing [OFFLINE]

**Rationale:** A full re-index of the 100+ TypeScript/TSX source files currently takes
3–8 minutes. This prevents ImpacTracer from being used after small, incremental code
changes during active development. SHA-256 hashing enables sub-30-second re-indexing
for 1–5 changed files.

**Schema:** See §4.2 for the `file_hashes` table definition.

**Algorithm:**
```python
def should_reindex_file(file_path: str, conn: sqlite3.Connection) -> bool:
    current_hash = hashlib.sha256(Path(file_path).read_bytes()).hexdigest()
    stored = conn.execute(
        "SELECT content_hash FROM file_hashes WHERE file_path=?", (file_path,)
    ).fetchone()
    return stored is None or stored[0] != current_hash

def update_file_hash(file_path: str, conn: sqlite3.Connection) -> None:
    current_hash = hashlib.sha256(Path(file_path).read_bytes()).hexdigest()
    conn.execute(
        "INSERT OR REPLACE INTO file_hashes(file_path, content_hash, indexed_at) "
        "VALUES (?, ?, ?)",
        (file_path, current_hash, datetime.utcnow().isoformat()),
    )
```

**Dependency-Aware Re-index:**
When file A changes, edges with `source_id LIKE 'A::%'` must also be re-extracted.
However, edges where A is the *target* (callers of A's functions) must be re-evaluated.
Safe approach: on re-index of file A, delete all `structural_edges` where either
`source_id LIKE 'A::%'` OR `target_id LIKE 'A::%'` (using the file-path prefix of
node IDs), then re-run Pass 2 for A and all files that import A. Track importer files
via a new `file_dependencies` auxiliary table (populated during Pass 2).

**Schema Addition:**
```sql
CREATE TABLE IF NOT EXISTS file_dependencies (
    dependent_file  TEXT NOT NULL,  -- file that imports or calls target_file
    target_file     TEXT NOT NULL,  -- file being depended on
    PRIMARY KEY (dependent_file, target_file)
);
```

---

#### P4.A.5 — Fix I: Layer-Aware Affinity Filter [BOTH]

**Rationale:** In v3.3, `compute_doc_code_candidates()` stores any code-node/doc-chunk
pair whose cosine similarity exceeds 0.60, regardless of whether the pairing is
architecturally meaningful. A `TYPE_DEFINITION` node for a database schema type
(e.g., `ICommissionListing`) frequently achieves similarity > 0.60 with SRS sections
about business processes — but a database type node linked to a business-process
requirement document is not a meaningful traceability relationship. This produces
backlinks in the synthesis report that confuse the LLM and reduce the signal-to-noise
ratio of the traceability evidence.

**Layer Compatibility Matrix:**
Define `LAYER_COMPAT: dict[str, dict[str, float]]` as a multiplier on the cosine
similarity before applying the `min_traceability_similarity` threshold. Values ∈ (0, 1];
values close to 0 effectively block the pairing.

```python
# In traceability.py
# Rows: code_node.file_classification  Cols: doc_chunk.chunk_type
LAYER_COMPAT: dict[str, dict[str, float]] = {
    "UI_COMPONENT":    {"FR": 0.80, "NFR": 0.40, "Design": 0.95, "Architecture": 0.50},
    "PAGE_COMPONENT":  {"FR": 0.85, "NFR": 0.40, "Design": 0.95, "Architecture": 0.55},
    "API_ROUTE":       {"FR": 0.90, "NFR": 0.60, "Design": 0.85, "Architecture": 0.70},
    "UTILITY":         {"FR": 0.55, "NFR": 0.50, "Design": 0.65, "Architecture": 0.80},
    "TYPE_DEFINITION": {"FR": 0.35, "NFR": 0.20, "Design": 0.70, "Architecture": 0.90},
    None:              {"FR": 0.70, "NFR": 0.50, "Design": 0.80, "Architecture": 0.70},
}
```

**Application in `compute_doc_code_candidates()`:**
```python
for i, cid in enumerate(code_ids):
    for j, did in enumerate(doc_ids):
        raw_score = float(sim[i, j])
        code_layer = code_metadata[cid]["file_classification"]   # from SQLite
        doc_type   = doc_metadata[did]["chunk_type"]             # from ChromaDB metadata
        compat     = LAYER_COMPAT.get(code_layer, LAYER_COMPAT[None]).get(doc_type, 0.70)
        adjusted   = raw_score * compat
        if adjusted >= min_similarity:
            results.append((cid, did, adjusted))
```

**Retrieval Affinity (online pipeline, S3.7):**
After the plausibility gate, apply a layer affinity check as a soft re-score for
candidates whose `file_classification` is misaligned with the CR's `affected_layers`:
```python
# A CR with affected_layers=["code", "design"] should not surface pure
# TYPE_DEFINITION nodes as high-priority candidates.
for cand in candidates:
    layer_penalty = RETRIEVAL_LAYER_PENALTY.get(
        (cand.get("file_classification"), cr_interp.change_type), 1.0
    )
    cand["reranker_score"] *= layer_penalty
candidates.sort(key=lambda c: c["reranker_score"], reverse=True)
```

The formal derivation is provided in §6.2.

---

### Phase B — API Routing & Skeletonization (High ROI, Moderate Complexity)

*Addresses the UI-layer Recall miss for API-connected components and improves the
quality of LLM Call #2 by replacing naive character slicing with semantic skeletonization.
Requires new AST passes and one new SQLite column. Estimated implementation: 3–4 days.*

---

#### P4.B.1 — AST Skeletonization: Primary (SIS Validation) [OFFLINE + ONLINE]

**Rationale**: In v3.3, the retrieval step maps ChromaDB's documents field (which stores the embed_text consisting only of the docstring and signature) to a text_snippet field. validator.py sends this text_snippet to LLM Call #2. Because the LLM only sees the docstring and signature, it is completely blind to the internal logic of the function. It receives no information about the control flow, external calls, or data dependencies, forcing it to guess the impact based solely on the "cover of the book."

A  **skeleton** representation solves this by using the full source_code stored in SQLite to fold syntactic boilerplate while preserving the semantically dense elements: imports, function signatures, external calls, conditional branches, type annotations, and return structure.

**Skeletonization Algorithm (Two-Pass Tag-and-Fold):**

Computed at index time by a new `_skeletonize_node()` function in `code_indexer.py`. 
To prevent folding parent nodes that contain critical nested logic (e.g., inline JSX callbacks), the algorithm operates in two passes:

* **Pass 1 (Tagging):** Walk the AST and tag all "High-Signal" nodes (`call_expression`, `return_statement`, `throw_statement`, `import_declaration`). Mark these nodes and all their ancestors as `DO_NOT_ERASE`.
* **Pass 2 (Folding):** Walk the AST bottom-up. Apply the following fold rules, but if a node is tagged `DO_NOT_ERASE`, fold its non-essential children instead of the node itself.

| AST Node Type | Folding Action & Exceptions |
|---|---|
| `jsx_element`, `jsx_self_closing` | Fold to `/* [JSX: N elements] */`. <br>**Exception:** If tagged `DO_NOT_ERASE` (contains inline callback), preserve the callback and fold sibling JSX nodes. |
| `array` (> 3 items) | Fold to `/* [array: N items] */`. <br>**Exception:** DO NOT fold if the array is an argument to a React Hook (`useEffect`, `useCallback`, `useMemo`). |
| `object` (> 4 props) | Fold to `/* [object: N props] */`. <br>**Exception:** If tagged `DO_NOT_ERASE` (contains nested method calls), preserve the method. |
| `if_statement`, `switch_statement` | Fold body to `/* [logic block] */`. <br>**Exception:** Preserve full body if it contains a `return`, `throw`, or `call_expression`. |
| `template_string` (> 100 chars) | Fold to `` `/* [template: N chars] */` ``. |
| `string_literal` (> 80 chars) | Fold to `"/* [string: N chars] */"`. |
| `comment` block | Remove entirely. |
| `import_declaration` | **Keep verbatim** (Critical for dependency resolution). |
| `call_expression` | **Keep verbatim** (Critical for execution tracking). |

**Output Example:**

Before (v3 text_snippet[:400]):
```typescript
/**
 * Renders a card for a commission listing with actions.
 * @param props - CommissionListingCardProps
 */
function CommissionListingCard({ listing, onDuplicate }: CommissionListingCardProps)
```
*(The LLM sees this and nothing else — no logic, no state, no API calls)*

After (v4 skeleton_snippet):
```typescript
function CommissionListingCard({ listing, onDuplicate }: CommissionListingCardProps) {
  const [isExpanded, setIsExpanded] = useState(false);
  const [duplicating, setDuplicating] = useState(false);
  async function handleDuplicate() {
    setDuplicating(true);
    await onDuplicate(listing.id);   // ← external call preserved
    setDuplicating(false);
  }
  return /* [JSX: 14 elements] */;   // ← boilerplate folded
}
```

The skeleton preserves 100% of the semantically relevant external calls, props, and control flow that the LLM needs to evaluate impact, which were previously entirely missing.

**Schema Addition:** See §4.1 — `skeleton_snippet TEXT` column in `code_nodes`.

**Integration in `validator.py`:**
```python
# Before (v3):
f"Snippet: {c.get('text_snippet', '')}"

# After (v4):
snippet = c.get("skeleton_snippet") or c.get("text_snippet", "")
f"Snippet: {snippet}"
```

**Settings Exposure:**
```python
use_skeleton_snippet_for_validation: bool = True   # in config.py
```

---

#### P4.B.2 — API Route String Matching: The Client→Server Bridge [OFFLINE]

**Rationale:** In our codebase, client UI components invoke server-side logic exclusively
through HTTP API routes (confirmed: no Server Actions are used). The call chain is:
`UI Component → fetch('/api/commissions/...') → app/api/commissions/route.ts → Service`.
In v3, there are no edges crossing this HTTP boundary. When a CR changes a service
function that is invoked via an API route, no client UI component is ever reached by BFS.
This is the architectural root cause of the UI-layer Recall miss.

**Implementation — Two-Pass Resolution:**

*Pass 2A: String extraction.* Scan all TypeScript/TSX function bodies for string literals
matching the pattern `/api/<path>`. Use tree-sitter to find `string_literal` nodes whose
content matches `re.compile(r'^/api(/[\w\-\[\]{}]+)+$')`. Record:
`(source_node_id, api_path_string, source_file)`.

*Pass 2B: Route resolution.* For each `api_path_string`, resolve to a physical Next.js
route file using the App Router convention:
```python
def resolve_api_route(api_path: str, repo_root: Path) -> Path | None:
    # Strip /api prefix, map to app/api/[path]/route.ts
    # /api/commissions → app/api/commissions/route.ts
    # /api/commissions/${id}/duplicate → app/api/commissions/[id]/duplicate/route.ts
    # Dynamic segments (${...} or :param) → [param]
    normalized = re.sub(r'\$\{[^}]+\}', '[id]', api_path.lstrip('/api'))
    normalized = re.sub(r':[a-zA-Z]+', '[id]', normalized)
    candidate = repo_root / "src" / "app" / "api" / normalized / "route.ts"
    if candidate.exists():
        return candidate
    # Pages Router fallback: pages/api/<path>.ts
    candidate_pages = repo_root / "src" / "pages" / "api" / (normalized + ".ts")
    if candidate_pages.exists():
        return candidate_pages
    return None
```

*Edge creation.* If the route file exists in `code_nodes` (it will, as an `API_ROUTE`
node), create:
```
source_node_id --[CLIENT_API_CALLS]--> route_file_node_id
```
Edge direction semantics: `CLIENT_API_CALLS` is stored as source→target in the graph
(same as CALLS). BFS traversal is **reverse** at max_depth=1: "find all clients that
call this route when the route changes." Forward at max_depth=1: "find the route that
this client depends on."

**BFS Configuration Addition:**
```python
"CLIENT_API_CALLS": {"direction": "reverse", "max_depth": 1},
```

**Coverage in our codebase:** The SQLite inspection confirms API call strings including
`/api/auth/login` (in `LoginForm`) and `/api/auth/register` (in `RegisterForm`). In the
commission listing domain, client components that call `/api/commissions/[id]/duplicate`
(once the feature is built) will automatically generate edges to the `app/api/commissions/
[id]/duplicate/route.ts` handler. When the handler's service call changes, `LoginForm`
and similar UI components are surfaced via reverse BFS traversal.

---

#### P4.B.3 — Dynamic Import Edge Detection (Dialog Layer) [OFFLINE]

**Rationale:** The codebase uses `dynamic()` imports in two confirmed locations:
1. `CommissionDialog.tsx` — loads `TosDialog` dynamically via `next/dynamic`.
2. `DialogManager.tsx` — renders `AuthDialog`, `ProfileDialog`, `UploadArtDialog`,
   `CommissionDialog`, `GalleryDialog`, and `TosDialog` conditionally.

In v3, no `IMPORTS` edge exists from `DialogManager` to any dialog it renders or from
`CommissionDialog` to `TosDialog`. When `TosDialog` changes (e.g., a new required terms
field is added), `CommissionDialog` and `DialogManager` — which are in the CIS path —
are invisible to BFS.

Note: `DialogManager` uses `useDialogStore` from Zustand, but only for UI/dialog state
(`dialog.type`, `dialog.entityId`, `dialog.isOwner`). No Zustand edges are required in
the graph; the `DYNAMIC_IMPORT` edge from `DialogManager` to each dialog component is
sufficient.

**AST Pattern (tree-sitter target):**
```typescript
// Pattern 1: next/dynamic import
const TosDialog = dynamic(() => import('./TosDialog'), { ssr: false })
//   call_expression: callee=identifier("dynamic")
//   argument[0]: arrow_function → call_expression: callee=identifier("import")
//     argument: string_literal("'./TosDialog'")

// Pattern 2: Standard conditional render in DialogManager
// (captured as RENDERS edge in v3 — no dynamic() call here)
// DialogManager renders statically imported components conditionally.
// Only the dynamic() pattern in CommissionDialog requires new extraction.
```

**Implementation in `_extract_edges_from_file()` (Pass 2):**
```python
def _extract_dynamic_import_edges(
    tree: Node, file_path: Path, node_map: dict, rel: str
) -> list[tuple[str, str, str]]:
    """Extract DYNAMIC_IMPORT edges from dynamic() call expressions."""
    edges = []
    for call in _find_nodes_by_type(tree, "call_expression"):
        callee = call.child_by_field_name("function")
        if callee and callee.text.decode() in ("dynamic",):
            # Extract the import path from the first arrow_function argument
            args = call.child_by_field_name("arguments")
            if args:
                inner_import_path = _extract_import_string_from_dynamic_arg(args)
                if inner_import_path:
                    resolved = _resolve_import_path(inner_import_path, file_path)
                    target_node_id = _make_file_node_id(resolved)
                    source_node_id = _make_file_node_id(file_path)
                    if target_node_id in node_map:
                        edges.append((source_node_id, target_node_id, "DYNAMIC_IMPORT"))
    return edges
```

**BFS Configuration Addition:**
```python
"DYNAMIC_IMPORT": {"direction": "reverse", "max_depth": 1},
# Semantics: if the dynamically imported component changes, find its dynamic importers.
```

---

### Phase C — Semantic Graph Enrichment (Moderate ROI, Higher Complexity)

*Adds three new edge types that model the most common implicit dependencies in
Next.js/React codebases: hook dependencies, prop callbacks, and field-level type access.
Requires tree-sitter-level AST traversal changes and careful type inference heuristics.
Estimated implementation: 4–6 days.*

---

#### P4.C.1 — React Hook Dependency Edges (`HOOK_DEPENDS_ON`) and Prop Callback Edges (`PASSES_CALLBACK`) [OFFLINE]

**Rationale — HOOK_DEPENDS_ON:**
React hooks with dependency arrays (`useEffect`, `useCallback`, `useMemo`,
`useLayoutEffect`) define explicit semantic dependencies: if a value in the array
changes, the hook's body re-executes. In v3, these dependencies are invisible:
```typescript
useEffect(() => {
    refetchCommissions(userId);  // body: external call — captured as CALLS
}, [userId, filterState]);       // dep array: if filterState changes, effect re-runs
                                 // → NO EDGE from filterState to this component in v3
```

**Rationale — PASSES_CALLBACK:**
JSX prop-callbacks are the dominant inter-component communication pattern:
```typescript
<CommissionListingCard
  listing={listing}
  onDuplicate={handleDuplicate}   // ← prop-callback: invisible in v3
/>
```
`handleDuplicate` is defined in the parent component. When `handleDuplicate`'s contract
changes (because `duplicateListing()` changes its signature), `CommissionListingCard`
must also be updated to match the new prop type. But in v3, no graph path exists from
`handleDuplicate` to `CommissionListingCard`.

**HOOK_DEPENDS_ON Extraction (AST pattern):**
```python
HOOK_NAMES = {"useEffect", "useCallback", "useMemo", "useLayoutEffect"}

for call in _find_call_expressions(tree):
    fn_name = _get_callee_name(call)
    if fn_name in HOOK_NAMES:
        dep_array = _get_nth_argument(call, index=-1)  # last arg is dep array
        if dep_array and dep_array.type == "array":
            for dep_id in _extract_identifiers(dep_array):
                # Resolve dep_id to its declaration node
                decl_node_id = _resolve_identifier_to_node(dep_id, file_scope)
                if decl_node_id:
                    edges.append((enclosing_fn_id, decl_node_id, "HOOK_DEPENDS_ON"))
```

**PASSES_CALLBACK Extraction (AST pattern):**
```python
ON_PREFIX_PATTERN = re.compile(r'^on[A-Z]')  # React convention: onXxx props

for jsx_attr in _find_jsx_attributes(tree):
    attr_name = _get_jsx_attr_name(jsx_attr)
    if ON_PREFIX_PATTERN.match(attr_name):
        attr_value = _get_jsx_attr_value(jsx_attr)
        # attr_value may be: {identifier} or {() => fn()} or {fn}
        referenced_fn = _extract_fn_reference(attr_value)
        if referenced_fn:
            decl_node_id = _resolve_identifier_to_node(referenced_fn, file_scope)
            if decl_node_id:
                parent_component_id = _get_jsx_element_component(jsx_attr)
                # PASSES_CALLBACK: parent passes fn to child component
                edges.append((parent_component_id, decl_node_id, "PASSES_CALLBACK"))
```

**BFS Configuration Additions:**
```python
"HOOK_DEPENDS_ON": {"direction": "reverse", "max_depth": 1},
# Semantics: if a depended-on value changes, find hooks that depend on it.

"PASSES_CALLBACK": {"direction": "forward", "max_depth": 1},
# Semantics: if a function is passed as a prop, find the child components
# that receive it. Forward traversal: fn → child_receiving_it.
```

**Scope Limitation (Phase C design constraint):**
`HOOK_DEPENDS_ON` is bounded at max_depth=1 to prevent chained hook dependencies from
producing a combinatorial explosion. `PASSES_CALLBACK` is forward direction at
max_depth=1 for the same reason. Both edge types operate as precision-enhancing recall
signals, not as primary graph traversal paths.

---

#### P4.C.2 — Field-Resolution Type Traversal (`FIELDS_ACCESSED`) [OFFLINE]

**Rationale:**
The v3 `TYPED_BY` edge links *any* function using type `T` to `T`'s declaration. For a
large interface like `ICommissionListing` (which has 15+ fields: id, price, slots,
status, milestones, revisionPolicy, cancellationPolicy, etc.), a CR that modifies only
the `price` field would flag every function that mentions `ICommissionListing` anywhere
in its signature or body — including functions that only access `listing.status` or
`listing.slots`. This produces false positives at the type level: functions that are
not affected by the specific field change are included in the CIS.

**Proposed Edge Type:**
```
source_fn --[FIELDS_ACCESSED(ICommissionListing.price)]--> ICommissionListing::price
```
Where `ICommissionListing::price` is a new synthetic node type `InterfaceField` in
`code_nodes`, representing a specific property of an interface.

**Implementation — Two-pass:**

*Pass 1 extension:* For each `Interface` node, extract all property declarations as
`InterfaceField` child nodes with `node_type = "InterfaceField"`,
`node_id = "<InterfaceId>::<fieldName>"`. Store in `code_nodes`.

*Pass 2 extension:* In function bodies, scan for `member_expression` patterns
(`X.fieldName`) where `X` is a parameter or variable annotated with type `T` and
`T.fieldName` exists in `code_nodes`. Create:
`(enclosing_fn_id, "T::fieldName", "FIELDS_ACCESSED")`.

Type inference heuristic: rather than full TypeScript type checker integration (which
would require `ts-morph` or TypeScript compiler API), use a conservative approximation:
- For function parameters with explicit type annotations (`param: ICommissionListing`),
  any `param.fieldName` access creates a `FIELDS_ACCESSED` edge to
  `ICommissionListing::fieldName`.
- For variables with `as` casts or object destructuring from a typed source.

**BFS Configuration Addition:**
```python
"FIELDS_ACCESSED": {"direction": "reverse", "max_depth": 2},
# Semantics: if a specific interface field changes, find all functions
# that access that specific field.
```

**Impact Precision:** For a CR modifying `ICommissionListing.price`, only functions
with `FIELDS_ACCESSED(ICommissionListing.price)` edges are reached, rather than all
functions with any `TYPED_BY(ICommissionListing)` edge. CIS cardinality reduction
estimated at 30–50% for type-change CRs affecting a single field of a large interface.

---

### Phase D — Synthesis Skeletonization (Optional / Conditional)

*P4.D.1 — AST Skeletonization: Secondary (Synthesis) [ONLINE]*

**Rationale:**
The v3.3 smoke test produced a synthesis context of ~3,241 tokens for a 12-node CIS —
well within the 100,000-token budget. However, for CRs with larger CIS sets (20–50 nodes,
which may occur for cross-cutting refactors or architectural changes), the token budget
may become constraining. The current fallback in `context_builder.py` truncates by
dropping lowest-significance nodes, which is a precision trade-off.

**Strategy:**
When `estimate_tokens(context) > SKELETON_THRESHOLD` (suggested: 60,000 tokens, 60% of
budget), the context builder falls back to `skeleton_snippet` for nodes ranked below the
top-N significance threshold, rather than dropping them entirely. This preserves the
structural justification evidence for all nodes while reducing token consumption per node
by approximately 60–70%.

```python
# In build_synthesis_context():
SKELETON_THRESHOLD = 60_000
if estimate_tokens(context) > SKELETON_THRESHOLD:
    # Rewrite lower-ranked nodes to use skeleton_snippet
    for item in context_items[TOP_N_FULL:]:
        if item.get("skeleton_snippet"):
            item["code_snippet"] = item["skeleton_snippet"]
    context = rebuild_context(context_items)
```

**Constraint:** This is marked OPTIONAL because (a) the current CIS sizes are well
within budget, and (b) using skeleton snippets for LLM Call #3 introduces a small risk
of omitting details the synthesizer needs for accurate causal chain descriptions. The
`use_skeleton_for_synthesis` config flag (default: `False`) must be explicitly enabled.

---

### v5.0 Long-Term Research Track

---

#### P5.0 — Continuous RLHF Feedback Loop

**Rationale:**
Every persistent failure mode in ImpacTracer (the `applySlotDelta` false positive being
the canonical example) is an instance of the same meta-problem: the system has no memory
between runs. It cannot learn from analyst corrections. Each new run executes with no
knowledge that a human analyst previously rejected a specific node for a specific class
of CR.

**Tier 1 — Negative Example Cache (Near-term, Feasible):**

Schema: See §4.4 (`analyst_corrections` table).

```python
# In runner.py, Step 3.5 (or as a pre-filter):
def apply_negative_cache(
    candidates: list[dict],
    cr_embedding: np.ndarray,
    conn: sqlite3.Connection,
    similarity_threshold: float = 0.85,
) -> list[dict]:
    """Block candidates that were rejected for semantically similar CRs."""
    stored = conn.execute(
        "SELECT cr_embedding, node_id FROM analyst_corrections WHERE verdict='FALSE_POSITIVE'"
    ).fetchall()
    blocked: set[str] = set()
    for stored_emb_bytes, node_id in stored:
        stored_emb = np.frombuffer(stored_emb_bytes, dtype=np.float32)
        similarity = float(np.dot(cr_embedding, stored_emb) /
                          (np.linalg.norm(cr_embedding) * np.linalg.norm(stored_emb)))
        if similarity >= similarity_threshold:
            blocked.add(node_id)
    filtered = [c for c in candidates if c["node_id"] not in blocked]
    logger.info("Negative cache: blocked {}/{} candidates", len(candidates)-len(filtered), len(candidates))
    return filtered
```

**Tier 2 — BGE-M3 Contrastive Fine-Tuning (Long-term, Research):**

After accumulating 50–100 analyst-correction triples, fine-tune the BGE-M3 embedding
model using InfoNCE contrastive loss:

```
L = -log[ exp(sim(e_CR, e_true) / τ) / Σ_n exp(sim(e_CR, e_neg_n) / τ) ]
```

Where:
- `e_CR` = embedding of the CR text
- `e_true` = embedding of confirmed true-positive code nodes
- `e_neg_n` = embeddings of analyst-rejected false-positive nodes for similar CRs
- `τ = 0.07` (temperature, standard for contrastive learning)

This geometrically pushes false-positive node embeddings (e.g., `applySlotDelta`) away
from the "duplicate listing" CR region in the embedding space. After fine-tuning,
`applySlotDelta`'s BGE-M3 vector would be less similar to any CR describing listing
creation — resolving the root cause at the vector space level rather than the
heuristic/gate level.

---

## 4. Database Schema Extensions

### 4.1 `code_nodes` — New Column [SCHEMA, OFFLINE]

```sql
ALTER TABLE code_nodes ADD COLUMN skeleton_snippet TEXT;
```

`skeleton_snippet` stores the tree-sitter-reduced skeleton of the function or component
body (see §P4.B.1). It is `NULL` for nodes where skeletonization is not applicable
(e.g., `TypeAlias`, `Interface`, `ExternalPackage`, `File` node types) or where the
source is shorter than the skeleton threshold (< 200 chars — no benefit from folding).

**Note on Backward Compatibility**: In v3.3, text_snippet is not a real database column; it is an alias assigned at retrieval time from ChromaDB's documents field (which stores the embed_text containing the docstring+signature). The full original source_code and the embed_text columns remain untouched in SQLite. The fallback mode (use_skeleton_snippet_for_validation: False) will seamlessly revert to using the retrieved text_snippet.

**Column population:**
Populated by the new `_skeletonize_node()` function in `code_indexer.py` during Pass 1,
after `source_code` is extracted. 
Stored directly in SQLite at index time. Not embedded; used only at validation and synthesis time.

---

### 4.2 `file_hashes` — New Table [SCHEMA, OFFLINE]

```sql
CREATE TABLE IF NOT EXISTS file_hashes (
    file_path    TEXT      NOT NULL PRIMARY KEY,  -- absolute or repo-relative path
    content_hash TEXT      NOT NULL,              -- SHA-256 hex digest of file bytes
    indexed_at   TIMESTAMP NOT NULL               -- ISO-8601 UTC timestamp
);
```

**Usage:** Populated during `impactracer index`. Before processing each TypeScript/TSX
file, `should_reindex_file()` queries this table. Updated after successful indexing of
each file via `update_file_hash()`.

**Consistency guarantee:** The `content_hash` reflects the exact bytes used to produce
the current `code_nodes` and `structural_edges` rows for that file. If the file is
modified between indexing runs, the hash mismatch triggers a clean re-extraction of all
nodes and edges from that file.

---

### 4.3 `file_dependencies` — New Table [SCHEMA, OFFLINE]

```sql
CREATE TABLE IF NOT EXISTS file_dependencies (
    dependent_file TEXT NOT NULL,  -- file that imports from target_file
    target_file    TEXT NOT NULL,  -- file being imported
    PRIMARY KEY (dependent_file, target_file)
);
```

**Usage:** Populated during Pass 2 edge extraction. For each `IMPORTS` edge extracted,
record `(source_file, target_file)` in `file_dependencies`. During incremental re-index
of `target_file`, query this table to identify `dependent_file`s whose edges must also
be re-evaluated.

---

### 4.4 `analyst_corrections` — New Table [SCHEMA, v5.0 Tier 1]

```sql
CREATE TABLE IF NOT EXISTS analyst_corrections (
    id             INTEGER   NOT NULL PRIMARY KEY AUTOINCREMENT,
    cr_text_hash   TEXT      NOT NULL,  -- SHA-256 of the CR text (for dedup)
    cr_embedding   BLOB      NOT NULL,  -- 1024-dim float32 BGE-M3 embedding of CR
    node_id        TEXT      NOT NULL,  -- code node that was judged
    verdict        TEXT      NOT NULL   -- 'FALSE_POSITIVE' or 'MISSED_TRUE_POSITIVE'
                             CHECK(verdict IN ('FALSE_POSITIVE', 'MISSED_TRUE_POSITIVE')),
    analyst_notes  TEXT,                -- optional free-text explanation
    recorded_at    TIMESTAMP NOT NULL   -- ISO-8601 UTC timestamp
);

CREATE INDEX IF NOT EXISTS idx_corrections_verdict ON analyst_corrections(verdict);
CREATE INDEX IF NOT EXISTS idx_corrections_node    ON analyst_corrections(node_id);
```

**Population Mechanism:** A new CLI command `impactracer correct` (v5.0 scope) accepts
a JSON file with analyst corrections in the format:
```json
{
  "cr_text": "...",
  "corrections": [
    {"node_id": "...", "verdict": "FALSE_POSITIVE", "notes": "..."},
    {"node_id": "...", "verdict": "MISSED_TRUE_POSITIVE", "notes": "..."}
  ]
}
```

---

### 4.5 Complete v4.0 Schema Overview

```
┌─────────────────────────────────────────────────────────────┐
│  code_nodes                                                  │
│  ─────────────────────────────────────────────────────────  │
│  node_id TEXT PK          node_type TEXT                    │
│  name TEXT                file_path TEXT                    │
│  line_start INTEGER        line_end INTEGER                  │
│  signature TEXT            docstring TEXT                   │
│  source_code TEXT          route_path TEXT                  │
│  file_classification TEXT  exported INTEGER                 │
│  embed_text TEXT                                            │
│  skeleton_snippet TEXT  ← [NEW v4.0 — P4.B.1]             │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│  structural_edges                                            │
│  ─────────────────────────────────────────────────────────  │
│  source_id TEXT            target_id TEXT                   │
│  edge_type TEXT  ← extended with 5 new types (§5)          │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│  doc_code_candidates                                         │
│  ─────────────────────────────────────────────────────────  │
│  code_id TEXT PK           doc_id TEXT PK                   │
│  similarity REAL  ← now layer_compat()-adjusted score       │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│  file_hashes  [NEW v4.0 — P4.A.4]                          │
│  ─────────────────────────────────────────────────────────  │
│  file_path TEXT PK         content_hash TEXT                │
│  indexed_at TIMESTAMP                                       │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│  file_dependencies  [NEW v4.0 — P4.A.4]                    │
│  ─────────────────────────────────────────────────────────  │
│  dependent_file TEXT PK    target_file TEXT PK              │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│  analyst_corrections  [NEW v5.0 — P5.0 Tier 1]             │
│  ─────────────────────────────────────────────────────────  │
│  id INTEGER PK AUTOINCREMENT                                │
│  cr_text_hash TEXT         cr_embedding BLOB                │
│  node_id TEXT              verdict TEXT                     │
│  analyst_notes TEXT        recorded_at TIMESTAMP            │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│  index_metadata  [EXISTING — new entries]                   │
│  ─────────────────────────────────────────────────────────  │
│  key TEXT PK               value TEXT                       │
│  ── new entries: ─────────────────────────────────────────  │
│  'skeletonization_enabled'  'true'                          │
│  'incremental_indexing'     'true'                          │
│  'edge_schema_version'      '4.0'                           │
└─────────────────────────────────────────────────────────────┘
```

---

## 5. AST Edge Catalog

### 5.1 Complete Edge Registry (v3 + v4.0 additions)

The following table is the authoritative edge type registry for ImpacTracer v4.0.
All edge types are stored in the `structural_edges.edge_type` column.

| Edge Type | Dir | v3 Depth | v4 Depth | Extraction Source | BFS Semantics |
|---|---|---|---|---|---|
| `CALLS` | reverse | 3 | 3 (high-conf) / 1 (low-conf, Fix D) | call_expression AST node | Callers of B are impacted when B changes |
| `INHERITS` | reverse | 3 | 3 | class_heritage clause | Subclasses impacted when parent changes |
| `IMPLEMENTS` | reverse | 3 | 3 | implements clause | Implementors must adapt when interface changes |
| `TYPED_BY` | reverse | 3 | 3 | type annotation, generic param | Functions using type T when T changes |
| `DEFINES_METHOD` | forward | 3 | 3 | class body method declarations | Class methods impacted when class changes |
| `IMPORTS` | reverse | 1 | 1 | import declarations (static) | Only direct importers of changed module |
| `DEPENDS_ON_EXTERNAL` | reverse | 1 | 1 | import from node_modules | Direct dependents of external package |
| `RENDERS` | reverse | 1 | 1 | JSX element usage in return | Parent components impacted when child changes |
| `CLIENT_API_CALLS` | reverse | — | 1 | fetch/axios string-to-route | UI clients impacted when their API route changes |
| `HOOK_DEPENDS_ON` | reverse | — | 1 | useEffect/useMemo dep arrays | Hook-containing component re-evaluates when dep changes |
| `PASSES_CALLBACK` | forward | — | 1 | JSX `onXxx={fn}` prop | Child components that receive and invoke a callback |
| `DYNAMIC_IMPORT` | reverse | — | 1 | `dynamic()` / `React.lazy()` | Dynamic importers impacted when the loaded module changes |
| `FIELDS_ACCESSED` | reverse | — | 2 | member_expression field access | Functions accessing specific interface field |

**Direction Legend:**
- `reverse`: Edge A→B in graph. B changes → find all A via in_edges(B). Impact flows backwards through dependency.
- `forward`: Edge A→B in graph. A changes → find all B via out_edges(A). Impact flows to owned/passed targets.

**v4.0 `EDGE_CONFIG` (graph_bfs.py):**
```python
EDGE_CONFIG: dict[str, dict] = {
    # v3.3 edges
    "CALLS":               {"direction": "reverse", "max_depth": 3},
    "INHERITS":            {"direction": "reverse", "max_depth": 3},
    "IMPLEMENTS":          {"direction": "reverse", "max_depth": 3},
    "TYPED_BY":            {"direction": "reverse", "max_depth": 3},
    "DEFINES_METHOD":      {"direction": "forward", "max_depth": 3},
    "IMPORTS":             {"direction": "reverse", "max_depth": 1},
    "DEPENDS_ON_EXTERNAL": {"direction": "reverse", "max_depth": 1},
    "RENDERS":             {"direction": "reverse", "max_depth": 1},
    # v4.0 additions
    "CLIENT_API_CALLS":    {"direction": "reverse", "max_depth": 1},  # P4.B.2
    "HOOK_DEPENDS_ON":     {"direction": "reverse", "max_depth": 1},  # P4.C.1
    "PASSES_CALLBACK":     {"direction": "forward", "max_depth": 1},  # P4.C.1
    "DYNAMIC_IMPORT":      {"direction": "reverse", "max_depth": 1},  # P4.B.3
    "FIELDS_ACCESSED":     {"direction": "reverse", "max_depth": 2},  # P4.C.2
}

# Updated _LOW_CONF_CAPPED_EDGES (Fix D — no change to behavioral cap)
_LOW_CONF_CAPPED_EDGES: frozenset[str] = frozenset({"CALLS"})
# New structural/reactive edges are NOT capped for low-confidence seeds:
# HOOK_DEPENDS_ON, PASSES_CALLBACK, FIELDS_ACCESSED represent compile-time
# semantic couplings that are categorically impacted regardless of confidence.
```

### 5.2 Edge Type Rationale Notes

**`CLIENT_API_CALLS` (depth 1, reverse):**
Depth is capped at 1 because the HTTP boundary is a natural isolation point. If
`app/api/commissions/[id]/duplicate/route.ts` changes, only the direct client callers
(UI components that call `/api/commissions/${id}/duplicate`) are immediately impacted.
Their transitive callers are reached through existing `CALLS` and `RENDERS` edges.

**`HOOK_DEPENDS_ON` (depth 1, reverse):**
Hook dependency is a direct semantic coupling. If value `X` changes, hooks with `X` in
their dependency array re-run. Depth 1 prevents the hook dependency graph from cascading
transitively: `useEffect([A])` in component C → `useEffect([C])` in component D would
be a depth-2 chain, but this represents a design pattern that should be caught by the
primary `RENDERS`/`IMPORTS` edges instead.

**`FIELDS_ACCESSED` (depth 2, reverse):**
Depth 2 allows discovery of functions that call helper functions that in turn access a
specific field. This is the most likely real-world coupling pattern: a validator function
`validateCommissionPrice(listing.price)` is called from `createListingFromForm` —
a depth-2 FIELDS_ACCESSED path surfaces `createListingFromForm` when
`ICommissionListing.price` changes.

---

## 6. Mathematical Formalisms

### 6.1 Adaptive Reciprocal Rank Fusion

**Standard RRF (v3.3):**

Let `D` be the set of all retrieved documents (candidates), `P = {p₁, p₂, p₃, p₄}` be
the four retrieval paths (dense-doc, BM25-doc, dense-code, BM25-code), and `rank_p(d)`
be the rank of document `d` in path `p`'s result list. The standard RRF score is:

```
RRF(d) = Σ_{p ∈ P} 1 / (k + rank_p(d))
```

where `k = 60` (bias constant, standard value from Cormack et al. 2009).

**Adaptive RRF (v4.0):**

Introduce a per-path weight function `w(p, τ, λ)` parameterized by:
- `τ` = `change_type` from `CRInterpretation` (e.g., `FEATURE_ADD`, `UI_CHANGE`)
- `λ` = `file_classification` of document `d` (e.g., `UI_COMPONENT`, `API_ROUTE`)

The layer affinity between path `p` and document layer `λ` is:

```
affinity(p, λ) = RETRIEVAL_AFFINITY[p][λ]
```

where `RETRIEVAL_AFFINITY` is a pre-defined matrix (see §P4.A.3 settings exposure).

The adaptive weight combines the CR-type weight and the layer affinity:

```
w(p, τ, λ) = W_type[τ][p] × affinity(p, λ)
```

The full **Adaptive RRF** score is:

```
ARRF(d, τ) = Σ_{p ∈ P} w(p, τ, λ_d) / (k + rank_p(d))
```

where `λ_d = file_classification(d)`.

**Normalization:** After computing ARRF scores for all candidates, normalize to [0, 1]:
```
ARRF_norm(d) = ARRF(d) / max_{d' ∈ D} ARRF(d')
```

**Example — UI_CHANGE CR, candidate = `CommissionListingPage` (UI_COMPONENT):**

```
W_type[UI_CHANGE][dense_code] = 2.0
affinity(dense_code, UI_COMPONENT) = 1.0  (perfect alignment)
→ w = 2.0 × 1.0 = 2.0

W_type[UI_CHANGE][bm25_doc] = 0.4
affinity(bm25_doc, UI_COMPONENT) = 0.5   (doc BM25 is low-affinity for UI nodes)
→ w = 0.4 × 0.5 = 0.2

ARRF(CommissionListingPage) = 2.0/(60 + rank_dense_code) + 0.2/(60 + rank_bm25_doc) + ...
```

**Example — same CR, candidate = `applySlotDelta` (UTILITY):**

```
W_type[UI_CHANGE][dense_code] = 2.0
affinity(dense_code, UTILITY) = 0.6   (code path still applies but lower affinity for UI CR)
→ w = 2.0 × 0.6 = 1.2

W_type[UI_CHANGE][bm25_doc] = 0.4
affinity(bm25_doc, UTILITY) = 0.7    (BM25 doc still applies for utility nodes)
→ w = 0.4 × 0.7 = 0.28

ARRF(applySlotDelta) ≈ 1.2/(60 + rank) + 0.28/(60 + rank) + ...
```

The ratio `ARRF(CommissionListingPage) / ARRF(applySlotDelta)` increases for UI CRs,
increasing the probability that `CommissionListingPage` outranks `applySlotDelta` in the
post-RRF candidate list.

**Property:** When all weights equal 1.0 (`_default` CR type), Adaptive RRF degenerates
exactly to standard RRF. Backward compatibility is preserved.

---

### 6.2 Layer-Aware Affinity Masking (Fix I)

**Problem Statement:**
Let `C` be the set of code nodes and `D` be the set of doc chunks. In v3, the
traceability similarity score is simply:

```
score_v3(c, d) = cosine_sim(embed(c), embed(d))
```

A pair `(c, d)` is stored in `doc_code_candidates` iff `score_v3(c, d) ≥ θ`
where `θ = 0.60` (min_traceability_similarity, v3.3).

The problem: a `TYPE_DEFINITION` node `c` with a short, generic embedding (e.g.,
`interface ID { id: string }`) may have `cosine_sim(embed(c), embed(d)) ≥ 0.60` for
a doc chunk `d` describing business processes, because both mention identifiers and
operations in the domain vocabulary. This produces a spurious traceability link that
the synthesis LLM may interpret as a meaningful relationship.

**Layer Compatibility Scoring:**

Define a compatibility multiplier `κ: FileClassification × ChunkType → (0, 1]`:

```
κ(file_class, chunk_type) =
  LAYER_COMPAT[file_class][chunk_type]  if file_class ∈ LAYER_COMPAT
  LAYER_COMPAT[None][chunk_type]        otherwise (default)
```

The **adjusted traceability score** is:

```
score_v4(c, d) = cosine_sim(embed(c), embed(d)) × κ(class(c), type(d))
```

A pair `(c, d)` is stored iff `score_v4(c, d) ≥ θ`.

**Effect Analysis:**

For a `TYPE_DEFINITION` node paired with an `FR` chunk:
```
κ(TYPE_DEFINITION, FR) = 0.35
```
Even if `cosine_sim = 0.70` (above threshold), the adjusted score is:
```
score_v4 = 0.70 × 0.35 = 0.245 < θ (0.60)
```
The pair is rejected. A function requirement directly linked to a type definition
document via a 0.35 compatibility multiplier requires `cosine_sim ≥ 0.60 / 0.35 = 1.71`,
which is impossible for a cosine similarity — effectively blocking all such pairings.

For a `SERVICE` (UTILITY-classified) function paired with a `Design` chunk:
```
κ(UTILITY, Design) = 0.65
score_v4 = 0.70 × 0.65 = 0.455 < θ
```
Only pairs with `cosine_sim ≥ 0.923` pass — a very high bar that ensures only strongly
semantically aligned service function / design document pairs appear in traceability.

For a `PAGE_COMPONENT` paired with a `Design` chunk:
```
κ(PAGE_COMPONENT, Design) = 0.95
score_v4 = 0.70 × 0.95 = 0.665 ≥ θ
```
This pair is stored — UI design sections linking to page components is an
architecturally valid and semantically meaningful traceability relationship.

**Information-Theoretic Motivation:**
The compatibility multiplier `κ` acts as a structured prior over the traceability matrix.
In software engineering, the layer structure (UI → Service → Repository → Type) implies
that meaningful traceability links are predominantly within-layer (UI ↔ UI design) and
cross-layer for direct dependencies (Service ↔ functional requirements), not arbitrary
cross-layer pairings (TypeAlias ↔ business process narratives). `κ` encodes this prior
as a soft filter, reducing the entropy of the stored traceability set and improving the
precision of the evidence presented to LLM Call #3.

**Expected Traceability Matrix Reduction:**
In v3.3, traceability pairs after Fix G: ~4,930.
With Fix I applied using the LAYER_COMPAT matrix above, estimated reduction to ~3,200–3,800 pairs
(further 23–35% reduction) through elimination of cross-layer noise. This compresses the
backlinks seen by LLM Call #3 to the most architecturally coherent evidence.

---

## 7. ImpacTracer v4.0 vs v3.3 Comparison

### 7.1 Pipeline Step Summary

| Step | v3.3 | v4.0 | Change |
|---|---|---|---|
| S0 | Load stores | Load stores | No change |
| S1 | CR Interpretation | CR Interpretation + `named_entry_points` | +1 LLM field |
| S2 | Uniform RRF | Adaptive RRF (cr_type-weighted) | MOD |
| S3 | Cross-encoder rerank | Cross-encoder rerank | No change |
| S3.5 | Score filter (0.01) | Score filter (0.01) | No change |
| S3.6 | — | Semantic deduplication gate | NEW |
| S3.7 | — | File-density plausibility gate + Fix I affinity mask | NEW |
| S4 | LLM + text_snippet (signature only) | LLM + skeleton_snippet | MOD |
| S5 | Seed resolution + Fix D | Seed resolution + Fix D | No change |
| S6 | BFS (8 edge types) | BFS (13 edge types) | +5 edge types |
| S7 | Backlinks + snippets | Backlinks + snippets | No change |
| S8 | Token-budgeted context | Token-budgeted context (skeleton fallback) | MOD (optional) |
| S9 | Synthesis | Synthesis | No change |
| Offline | Full re-index | Incremental re-index (SHA-256) | MOD |
| Offline | Skeletonization | NEW |
| Offline | API route matching | NEW |
| Offline | Hook/prop/dynamic edges | NEW |

### 7.2 Projected Impact on Pipeline Metrics

The following projections are theoretical estimates based on the v3.3 smoke test results
and the architectural analysis of each failure mode. They will be validated against the
Ground Truth evaluation dataset once available.

| Metric | v3.3 Observed | v4.0 Projected | Primary Driver |
|---|---|---|---|
| `applySlotDelta` in CIS | YES (FP) | **NO** | P4.A.2 Plausibility Gate |
| UI component in CIS (listing CR) | NO (FN) | **YES** | P4.B.2 CLIENT_API_CALLS |
| Precision@10 (service layer CRs) | ~0.25 | **~0.75–0.85** | P4.A.2 + P4.A.3 |
| Recall@10 (service layer CRs) | ~0.40 | **~0.55–0.70** | P4.B.2 + P4.C.1 |
| Traceability pairs (noise) | 4,930 | **~3,200–3,800** | P4.A.5 Fix I |
| Indexing time (1-2 file change) | 3–8 min | **< 30 sec** | P4.A.4 Incremental |
| Validator context quality (token efficiency) | text_snippet (signature only) | skeleton_snippet (full logic map) | P4.B.1 |
| False-positive cascade depth | depth 2 (9 nodes) | **depth 0 (0 nodes)** | P4.A.2 Gate |

---

## 8. Architectural Constraints and Non-Goals

The following are **explicitly out of scope** for v4.0, based on the testbed constraints
established for the ImpacTracer research system:

1. **No Git co-change mining.** All graph edges are derived from AST structure and code
   content alone. Co-change coupling from `git log` is architecturally excluded to
   maintain AST/Vector purity as a research invariant.

2. **No Server Action edges.** The target codebase uses zero Next.js Server Actions.
   All server-side invocations go through standard HTTP API routes, handled by the
   `CLIENT_API_CALLS` edge type (§P4.B.2).

3. **No Zustand state graph edges.** The codebase's Zustand stores (`dialogStore.ts`,
   `uiStore.ts`) manage exclusively dialog and UI state (theme, sidebar, profile views).
   No business logic or data mutation passes through Zustand. Therefore, no graph edges
   are needed to model Zustand as a data-flow conduit. The `useDialogStore()` calls in
   `DialogManager.tsx` are captured as `DEPENDS_ON_EXTERNAL` edges to the Zustand
   package, which is architecturally correct and sufficient.

4. **No TypeScript Compiler API integration.** Full type inference (for precise
   `FIELDS_ACCESSED` resolution) requires integration with the TypeScript compiler's
   type checker, which introduces a Node.js runtime dependency. The v4.0 `FIELDS_ACCESSED`
   implementation uses conservative AST heuristics (parameter type annotation matching)
   rather than full type flow analysis.

5. **No external vector database migration.** ChromaDB with PersistentClient remains
   the vector store. The improvements in §4–6 operate on the existing storage
   architecture.

---

## 9. Implementation Priority Matrix

Final ranked list of all v4.0 proposals, ordered by engineering ROI
(Impact × Feasibility / Implementation Complexity):

| Rank | Proposal | Phase | Impact | Feasibility | Est. Days |
|---|---|---|---|---|---|
| 1 | P4.A.2 — Semantic Seed Arbitration | A | ★★★★★ | ★★★★★ | 0.5 |
| 2 | P4.A.1 — Cross-Collection Deduplication | A | ★★★☆☆ | ★★★★★ | 0.5 |
| 3 | P4.A.3 — Adaptive RRF Weighting | A | ★★★★☆ | ★★★★☆ | 1 |
| 4 | P4.A.5 — Fix I Layer-Aware Affinity | A | ★★★☆☆ | ★★★★★ | 1 |
| 5 | P4.A.4 — Incremental Indexing | A | ★★★☆☆ | ★★★★☆ | 2 |
| 6 | P4.B.1 — AST Skeletonization (Validator) | B | ★★★★☆ | ★★★★☆ | 2 |
| 7 | P4.B.2 — API Route String Matching | B | ★★★★★ | ★★★★☆ | 2 |
| 8 | P4.B.3 — Dynamic Import Edges | B | ★★☆☆☆ | ★★★★★ | 0.5 |
| 9 | P4.C.1 — Hook + Prop Callback Edges | C | ★★★★☆ | ★★★☆☆ | 3 |
| 10 | P4.C.2 — Field-Resolution TYPED_BY | C | ★★★☆☆ | ★★☆☆☆ | 3 |
| 11 | P4.D.1 — Skeletonization (Synthesis) | D | ★★☆☆☆ | ★★★★★ | 0.5 |
| 12 | P5.0 Tier 1 — Negative Example Cache | v5.0 | ★★★★★ | ★★★☆☆ | 3 |
| 13 | P5.0 Tier 2 — BGE-M3 Fine-tuning | v5.0 | ★★★★★ | ★☆☆☆☆ | 10+ |

**Recommended MVP implementation order:**
Phase A (Proposals 1–5) in sequence → Phase B (6–8) → Phase C (9–10) if time permits.
Total Phase A+B estimated time: 9–10 days of focused implementation.

---

*End of ImpacTracer Master Blueprint v4.0*
*Prepared by: Lead Architect, ImpacTracer Research Project*
*Date: 2026-04-07*
*Review Status: APPROVED FOR IMPLEMENTATION — pending Ground Truth dataset completion*
