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
    The table plugin is required to parse GFM tables into structured nodes
    rather than raw paragraph text.
"""
from __future__ import annotations

import re
from pathlib import Path

import mistune
from mistune.plugins.table import table as table_plugin

CHUNK_TYPE_RULES: dict[str, list[str]] = {
    "FR":     ["kebutuhan fungsional", "functional requirement", "use case"],
    "NFR":    ["non-fungsional", "non-functional", "kebutuhan non"],
    "Design": ["perancangan", "desain", "arsitektur", "design", "architecture"],
}

# Module-level parser singleton: created once, reused across calls.
# The table plugin enables proper AST nodes for GFM tables (required for SDD).
_MD_PARSER = mistune.create_markdown(renderer=None, plugins=[table_plugin])


def classify_chunk(section_title: str) -> str:
    """Return chunk_type based on keyword matching against section_title."""
    t = section_title.lower()
    for ctype, keywords in CHUNK_TYPE_RULES.items():
        if any(kw in t for kw in keywords):
            return ctype
    return "General"


def _slugify(text: str) -> str:
    """Convert section title to a URL-safe identifier for chunk_id."""
    text = text.lower()
    # Remove characters that are not word chars, whitespace, or hyphens
    text = re.sub(r'[^\w\s-]', '', text, flags=re.UNICODE)
    # Collapse whitespace and underscores to single hyphens
    text = re.sub(r'[\s_]+', '-', text)
    # Collapse consecutive hyphens
    text = re.sub(r'-{2,}', '-', text)
    return text.strip('-')


def _inline_text(children: list[dict]) -> str:
    """Flatten a list of inline AST nodes to a plain-text string."""
    parts: list[str] = []
    for node in children:
        ntype = node.get('type', '')
        if ntype in ('softbreak', 'linebreak'):
            parts.append(' ')
        elif 'raw' in node:
            parts.append(node['raw'])
        elif 'children' in node:
            parts.append(_inline_text(node['children']))
    return ''.join(parts)


def _table_to_text(token: dict) -> str:
    """Convert a mistune table AST node to a pipe-delimited text block."""
    rows: list[str] = []
    for section in token.get('children', []):
        stype = section.get('type', '')
        if stype == 'table_head':
            # table_head children are table_cell nodes directly
            cells = [
                _inline_text(cell.get('children', []))
                for cell in section.get('children', [])
                if cell.get('type') == 'table_cell'
            ]
            if cells:
                rows.append(' | '.join(cells))
        elif stype == 'table_body':
            # table_body children are table_row nodes
            for row in section.get('children', []):
                if row.get('type') == 'table_row':
                    cells = [
                        _inline_text(cell.get('children', []))
                        for cell in row.get('children', [])
                        if cell.get('type') == 'table_cell'
                    ]
                    if cells:
                        rows.append(' | '.join(cells))
    return '\n'.join(rows)


def _block_to_text(token: dict) -> str:
    """Convert a single block-level AST token to plain text.

    Tokens that carry no semantic content (blank_line, thematic_break,
    block_html) return an empty string so they are filtered out in the
    join step.
    """
    ttype = token.get('type', '')

    if ttype in ('blank_line', 'thematic_break', 'block_html'):
        return ''

    if ttype == 'heading':
        level = token.get('attrs', {}).get('level', 1)
        title = _inline_text(token.get('children', []))
        return f"{'#' * level} {title}"

    if ttype in ('paragraph', 'block_text'):
        return _inline_text(token.get('children', []))

    if ttype == 'block_code':
        return token.get('raw', '')

    if ttype == 'table':
        return _table_to_text(token)

    if ttype == 'list':
        lines: list[str] = []
        for item in token.get('children', []):
            if item.get('type') == 'list_item':
                item_parts = [
                    _block_to_text(child)
                    for child in item.get('children', [])
                ]
                line = ' '.join(p for p in item_parts if p)
                lines.append(f'- {line}')
        return '\n'.join(lines)

    if ttype == 'block_quote':
        inner = '\n'.join(
            filter(None, (_block_to_text(c) for c in token.get('children', [])))
        )
        return inner

    # Generic fallback: leaf nodes have 'raw'; branch nodes have 'children'
    if 'raw' in token:
        return token['raw']
    if 'children' in token:
        return '\n'.join(
            filter(None, (_block_to_text(c) for c in token['children']))
        )
    return ''


def chunk_markdown(filepath: str) -> list[dict]:
    """Split a Markdown file into chunks at H2/H3 heading boundaries.

    Returns a list of dicts with keys:
        chunk_id      — "{filename}::{slugified_section_title}"
        source_file   — bare filename (e.g. "srs.md")
        section_title — heading text as written in the document
        chunk_type    — "FR" | "NFR" | "Design" | "General"
        text          — section_title + content, ready for embedding

    Tokens before the first H2/H3 (e.g. the H1 document title) are
    discarded — they carry no retrievable section semantics.
    H4+ headings are NOT split points; they accumulate into the
    enclosing H3 chunk as content text.
    """
    path = Path(filepath)
    filename = path.name
    raw_text = path.read_text(encoding='utf-8')

    tokens: list[dict] = _MD_PARSER(raw_text)

    chunks: list[dict] = []
    current_title: str | None = None
    current_tokens: list[dict] = []

    def _flush() -> None:
        if current_title is None:
            return
        content_lines = [_block_to_text(t) for t in current_tokens]
        content = '\n'.join(line for line in content_lines if line)
        text = (
            f"{current_title}\n\n{content}" if content else current_title
        ).strip()
        chunks.append({
            'chunk_id':      f"{filename}::{_slugify(current_title)}",
            'source_file':   filename,
            'section_title': current_title,
            'chunk_type':    classify_chunk(current_title),
            'text':          text,
        })

    for token in tokens:
        if token.get('type') == 'heading':
            level = token.get('attrs', {}).get('level', 1)
            if level in (2, 3):
                _flush()
                current_title = _inline_text(token.get('children', []))
                current_tokens = []
                continue
        # H4+ headings and all content blocks accumulate into current chunk
        current_tokens.append(token)

    _flush()  # flush the final open chunk
    return chunks


def index_docs(docs_dir: str) -> list[dict]:
    """Chunk all .md files in docs_dir and return the combined list.

    Files are processed in sorted order to guarantee determinism across
    operating systems.
    """
    all_chunks: list[dict] = []
    for path in sorted(Path(docs_dir).glob('*.md')):
        all_chunks.extend(chunk_markdown(str(path)))
    return all_chunks
