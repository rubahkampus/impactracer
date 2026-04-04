"""
Code Indexer — Tree-sitter AST Extraction for TypeScript/TSX
=============================================================

RESPONSIBILITY
    Parses all .ts and .tsx files in a repository using tree-sitter,
    extracts code nodes (8 types) and structural edges (8 types),
    and writes them to the SQLite code_nodes and structural_edges tables.

NODE TYPES EXTRACTED
    File, Class, Function, Method, Interface, TypeAlias, Enum,
    ExternalPackage.

EDGE TYPES EXTRACTED
    CALLS, INHERITS, IMPLEMENTS, TYPED_BY, DEFINES_METHOD,
    IMPORTS, DEPENDS_ON_EXTERNAL, RENDERS.

ARCHITECTURAL CONSTRAINTS
    Zero LLM calls. Purely deterministic AST traversal.
    Two-pass algorithm: Pass 1 extracts all nodes and builds the
    known_node_ids registry; Pass 2 extracts edges that reference
    the registry to prevent dangling edge targets.
    TSX files MUST use the tsx grammar (not typescript).
"""
from __future__ import annotations

import json
import re
import sqlite3
import warnings
from pathlib import Path

warnings.filterwarnings("ignore", category=FutureWarning)
from tree_sitter_languages import get_parser as _get_ts_parser_raw

# ── Constants ──────────────────────────────────────────────────────────

EXCLUDE_DIRS = frozenset({
    ".next", "node_modules", "__pycache__", ".git",
    "dist", "build", ".turbo", "out", "coverage",
})

EXCLUDE_SUFFIXES = (".test.ts", ".test.tsx", ".spec.ts", ".spec.tsx")

BUILTIN_PATTERNS = frozenset({
    "console", "Object", "Array", "Math", "JSON", "Promise",
    "setTimeout", "setInterval", "clearTimeout", "clearInterval",
    "parseInt", "parseFloat", "String", "Number", "Boolean",
    "Error", "Date", "RegExp", "Map", "Set", "WeakMap", "WeakSet",
    "Symbol", "Proxy", "Reflect", "Intl", "fetch", "URL",
    "URLSearchParams", "FormData", "Headers", "Request", "Response",
    "Buffer", "process", "require",
})

PRIMITIVE_TYPES = frozenset({
    "string", "number", "boolean", "void", "any", "unknown",
    "null", "undefined", "never", "object", "symbol", "bigint",
    "true", "false", "this",
    # Framework generics used as wrappers (target inside type_args handled separately)
    "Promise", "Array", "ReadonlyArray", "Readonly", "Partial",
    "Required", "Record", "NonNullable", "ReturnType", "InstanceType",
    "Omit", "Pick", "Exclude", "Extract", "Parameters",
    # React / Next.js types not in code_nodes
    "React", "ReactNode", "ReactElement", "FC", "FunctionComponent",
    "JSX", "Element", "NextRequest", "NextResponse",
    "SyntheticEvent", "MouseEvent", "KeyboardEvent", "ChangeEvent",
    "FormEvent", "HTMLElement", "HTMLInputElement", "HTMLDivElement",
    "Document", "Window", "Event", "EventTarget",
    # Mongoose / DB
    "Document", "Schema", "Model", "Types", "ObjectId",
})

NEXTJS_ROUTE_PATTERNS: dict[str, str] = {
    "route": "API_ROUTE",
    "page": "PAGE_COMPONENT",
    "layout": "PAGE_COMPONENT",
}


# ── Parser cache ──────────────────────────────────────────────────────

_parser_cache: dict[str, object] = {}

def _get_parser(file_path: Path):
    """Return the correct tree-sitter parser; TSX files must use tsx grammar."""
    lang = "tsx" if file_path.suffix == ".tsx" else "typescript"
    if lang not in _parser_cache:
        _parser_cache[lang] = _get_ts_parser_raw(lang)
    return _parser_cache[lang]


# ── File utilities ─────────────────────────────────────────────────────

def _find_ts_files(repo_root: Path) -> list[Path]:
    """Find all .ts/.tsx source files, excluding test files and build dirs."""
    results: list[Path] = []
    for suffix in ("*.ts", "*.tsx"):
        for p in repo_root.rglob(suffix):
            if any(part in EXCLUDE_DIRS for part in p.parts):
                continue
            if any(p.name.endswith(s) for s in EXCLUDE_SUFFIXES):
                continue
            results.append(p)
    return sorted(results)


def _load_package_deps(repo_root: Path) -> set[str]:
    """Load all dependency names from package.json."""
    pkg_file = repo_root / "package.json"
    if not pkg_file.exists():
        return set()
    try:
        pkg = json.loads(pkg_file.read_text(encoding="utf-8"))
        deps: set[str] = set()
        for section in ("dependencies", "devDependencies", "peerDependencies"):
            deps.update(pkg.get(section, {}).keys())
        return deps
    except Exception:
        return set()


def _load_alias_map(repo_root: Path) -> dict[str, str]:
    """Parse tsconfig.json paths into a prefix → replacement dict."""
    tsconfig_file = repo_root / "tsconfig.json"
    if not tsconfig_file.exists():
        return {}
    try:
        raw = tsconfig_file.read_text(encoding="utf-8")
        # Strip JS-style comments for basic parsing compatibility
        raw = re.sub(r"//.*", "", raw)
        cfg = json.loads(raw)
        paths = cfg.get("compilerOptions", {}).get("paths", {})
        base_url = cfg.get("compilerOptions", {}).get("baseUrl", ".")
        alias_map: dict[str, str] = {}
        for alias, targets in paths.items():
            if not targets:
                continue
            # alias: "@/*", target: ["src/*"] → "@/" → "src/"
            alias_prefix = alias.rstrip("*")
            target_prefix = targets[0].rstrip("*")
            # Make target relative to baseUrl
            alias_map[alias_prefix] = str(
                (repo_root / base_url / target_prefix)
                .resolve()
                .relative_to(repo_root)
            ).replace("\\", "/").rstrip("/") + "/"
        return alias_map
    except Exception:
        return {}


def _rel_path(file_path: Path, repo_root: Path) -> str:
    """Normalized repo-relative path with forward slashes."""
    return str(file_path.relative_to(repo_root)).replace("\\", "/")


def _classify_file(rel_path: str) -> str | None:
    """Map a file's relative path to a file_classification value."""
    parts = rel_path.replace("\\", "/").split("/")
    filename_stem = parts[-1].split(".")[0]  # e.g. "route" from "route.ts"

    if "app" in parts:
        cls = NEXTJS_ROUTE_PATTERNS.get(filename_stem)
        if cls:
            return cls

    for part in parts[:-1]:
        if part == "components":
            return "UI_COMPONENT"
        if part in ("lib", "utils", "hooks", "middleware", "theme"):
            return "UTILITY"
        if part == "types":
            return "TYPE_DEFINITION"
    return None


def _derive_route_path(rel_path: str) -> str | None:
    """Derive Next.js route from file path, e.g. app/api/users/[id]/route.ts → /api/users/{id}."""
    parts = rel_path.replace("\\", "/").split("/")
    try:
        app_idx = parts.index("app")
    except ValueError:
        return None

    filename = parts[-1]
    if not any(filename.startswith(p) for p in ("route.", "page.", "layout.")):
        return None

    route_parts = parts[app_idx + 1: -1]  # segments between 'app' and filename
    converted = [re.sub(r"\[(.+?)\]", r"{\1}", p) for p in route_parts]
    return "/" + "/".join(converted) if converted else "/"


# ── Source text helpers ────────────────────────────────────────────────

def _node_text(node, source_bytes: bytes) -> str:
    return source_bytes[node.start_byte: node.end_byte].decode("utf-8", "replace")


def _extract_preceding_jsdoc(node_start_row: int, source_lines: list[str]) -> str | None:
    """Scan backwards from the node's start line for an immediately preceding /** */ block."""
    line = node_start_row - 1
    while line >= 0 and source_lines[line].strip() == "":
        line -= 1
    if line < 0:
        return None
    if not source_lines[line].strip().endswith("*/"):
        return None
    end = line
    while line >= 0 and "/**" not in source_lines[line]:
        line -= 1
    if line < 0 or "/**" not in source_lines[line]:
        return None
    return "\n".join(s.strip() for s in source_lines[line: end + 1])


def _get_signature(decl_node, name: str, source_bytes: bytes) -> str:
    """Extract the signature portion of a declaration (everything before the body)."""
    body = decl_node.child_by_field_name("body")
    if body is None:
        # Interfaces, TypeAliases, Enums have no body field by that name
        # Use the full text but cap it
        return _node_text(decl_node, source_bytes)[:300]
    sig = source_bytes[decl_node.start_byte: body.start_byte].decode("utf-8", "replace").strip()
    return sig


def _has_jsx(node) -> bool:
    """Return True if any descendant of node is a JSX element (React component body check)."""
    if node.type in ("jsx_element", "jsx_self_closing_element", "jsx_fragment"):
        return True
    for child in node.children:
        if _has_jsx(child):
            return True
    return False


def _make_embed_text(name: str, signature: str, docstring: str | None) -> str:
    """Compose embed text: docstring + signature per Blueprint Section 6.2."""
    parts = []
    if docstring:
        parts.append(docstring)
    if signature:
        parts.append(signature)
    elif name:
        parts.append(name)
    return "\n".join(parts)


# ── Type identifier extraction for TYPED_BY ────────────────────────────

def _collect_type_identifiers(type_node) -> list[str]:
    """Recursively collect all non-primitive type_identifier names from a type expression."""
    if type_node is None:
        return []
    ntype = type_node.type
    if ntype == "type_identifier":
        name = type_node.text.decode("utf-8", "replace")
        return [] if name in PRIMITIVE_TYPES else [name]
    if ntype in ("predefined_type", "literal_type", "this_type", "template_literal_type"):
        return []
    results: list[str] = []
    for child in type_node.named_children:
        results.extend(_collect_type_identifiers(child))
    return results


def _extract_type_annotations(params_node, return_type_node) -> list[str]:
    """Extract all user-defined type names from function parameters and return type."""
    names: list[str] = []
    if params_node:
        for param in params_node.named_children:
            if param.type in ("required_parameter", "optional_parameter", "rest_parameter"):
                ta = param.child_by_field_name("type")
                if ta is None:
                    # Fallback: look for type_annotation child
                    for c in param.named_children:
                        if c.type == "type_annotation":
                            ta = c.named_children[0] if c.named_children else None
                            break
                if ta:
                    names.extend(_collect_type_identifiers(ta))
    if return_type_node:
        inner = return_type_node.named_children
        if inner:
            names.extend(_collect_type_identifiers(inner[0]))
    return names


# ── Import resolution ──────────────────────────────────────────────────

_TS_EXTS = (".ts", ".tsx", ".js", ".jsx")
_INDEX_SUFFIXES = ("/index.ts", "/index.tsx", "/index.js")


def _resolve_import_path(
    import_source: str,
    current_file: Path,
    repo_root: Path,
    alias_map: dict[str, str],
) -> str | None:
    """
    Resolve an import source string to a repo-relative file path.
    Returns None for unresolvable or external imports.
    """
    # Apply path aliases (e.g. "@/" → "src/")
    resolved = import_source
    for alias_prefix, real_prefix in alias_map.items():
        if resolved.startswith(alias_prefix):
            resolved = real_prefix + resolved[len(alias_prefix):]
            break

    if not (resolved.startswith(".") or resolved.startswith("/")):
        return None  # External package

    # Absolute or relative path
    if resolved.startswith("/"):
        base = repo_root / resolved.lstrip("/")
    else:
        base = current_file.parent / resolved

    # If it already has an extension and the file exists, use as-is
    if base.suffix in (".ts", ".tsx", ".js", ".jsx") and base.exists():
        try:
            return str(base.resolve().relative_to(repo_root)).replace("\\", "/")
        except ValueError:
            return None

    # Try adding extensions
    for ext in _TS_EXTS:
        candidate = base.parent / (base.name + ext)
        if candidate.exists():
            try:
                return str(candidate.resolve().relative_to(repo_root)).replace("\\", "/")
            except ValueError:
                pass

    # Try index file
    for suffix in _INDEX_SUFFIXES:
        candidate = repo_root / (str(base.relative_to(repo_root) if base.is_absolute() else base).replace("\\", "/") + suffix.lstrip("/"))
        idx = Path(str(base) + suffix.replace("/", "/"))
        if idx.exists():
            try:
                return str(idx.resolve().relative_to(repo_root)).replace("\\", "/")
            except ValueError:
                pass

    return None


def _get_import_source(import_node) -> str | None:
    """Extract the import source string from an import_statement node."""
    for c in import_node.named_children:
        if c.type == "string":
            # The string_fragment child holds the actual value
            for frag in c.named_children:
                if frag.type == "string_fragment":
                    return frag.text.decode("utf-8", "replace")
            # Fallback: strip quotes from the string text
            raw = c.text.decode("utf-8", "replace").strip("'\"")
            return raw
    return None


# ── Node extraction helpers ────────────────────────────────────────────

def _make_node(
    node_id: str,
    node_type: str,
    name: str,
    file_path: str,
    line_start: int | None,
    line_end: int | None,
    signature: str | None,
    docstring: str | None,
    source_code: str | None,
    route_path: str | None,
    file_classification: str | None,
    exported: bool,
) -> dict:
    embed_text = _make_embed_text(name, signature or "", docstring)
    return {
        "node_id": node_id,
        "node_type": node_type,
        "name": name,
        "file_path": file_path,
        "line_start": line_start,
        "line_end": line_end,
        "signature": signature,
        "docstring": docstring,
        "source_code": source_code,
        "route_path": route_path,
        "file_classification": file_classification,
        "exported": 1 if exported else 0,
        "embed_text": embed_text,
    }


def _find_all(node, target_type: str) -> list:
    """Yield all descendant nodes of a given type (breadth-first)."""
    results = []
    queue = list(node.named_children)
    while queue:
        n = queue.pop(0)
        if n.type == target_type:
            results.append(n)
        queue.extend(n.named_children)
    return results


def _find_in_body_no_cross(node, target_types: set[str]) -> list:
    """
    Find descendant nodes of target_types within node, but do NOT recurse
    into named function/class declarations (they are separately indexed nodes).
    """
    STOP_TYPES = frozenset({
        "function_declaration", "method_definition",
        "class_declaration",
    })
    results = []
    for child in node.named_children:
        if child.type in target_types:
            results.append(child)
        if child.type not in STOP_TYPES:
            results.extend(_find_in_body_no_cross(child, target_types))
    return results


# ── Pass 1: Extract nodes from a single file ───────────────────────────

def _extract_nodes_from_file(
    file_path: Path,
    repo_root: Path,
    source_bytes: bytes,
) -> list[dict]:
    """
    Parse one TypeScript/TSX file and extract all code node dicts.
    Does NOT extract edges (pass 2 handles that).
    """
    rel = _rel_path(file_path, repo_root)
    file_cls = _classify_file(rel)
    route_path = _derive_route_path(rel)
    source_lines = source_bytes.decode("utf-8", "replace").split("\n")

    parser = _get_parser(file_path)
    tree = parser.parse(source_bytes)
    root = tree.root_node

    nodes: list[dict] = []

    # File node
    nodes.append(_make_node(
        node_id=rel,
        node_type="File",
        name=file_path.name,
        file_path=rel,
        line_start=1,
        line_end=len(source_lines),
        signature=None,
        docstring=None,
        source_code=None,
        route_path=route_path,
        file_classification=file_cls,
        exported=False,
    ))

    def _process_decl(decl_node, exported: bool) -> None:
        """Extract a node from a single top-level declaration node."""
        ntype = decl_node.type

        # ── function_declaration ────────────────────────────────────
        if ntype == "function_declaration":
            name_node = decl_node.child_by_field_name("name")
            if name_node is None:
                return  # anonymous function, skip
            name = name_node.text.decode("utf-8", "replace")
            line_start = decl_node.start_point[0] + 1
            line_end = decl_node.end_point[0] + 1
            sig = _get_signature(decl_node, name, source_bytes)
            doc = _extract_preceding_jsdoc(decl_node.start_point[0], source_lines)
            src = _node_text(decl_node, source_bytes)
            nodes.append(_make_node(
                node_id=f"{rel}::{name}",
                node_type="Function",
                name=name,
                file_path=rel,
                line_start=line_start,
                line_end=line_end,
                signature=sig,
                docstring=doc,
                source_code=src,
                route_path=None,
                file_classification=file_cls,
                exported=exported,
            ))

        # ── lexical_declaration (arrow functions) ──────────────────
        elif ntype == "lexical_declaration":
            for vd in decl_node.named_children:
                if vd.type != "variable_declarator":
                    continue
                name_node = vd.child_by_field_name("name")
                value_node = vd.child_by_field_name("value")
                if name_node is None or value_node is None:
                    continue
                if value_node.type != "arrow_function":
                    continue
                name = name_node.text.decode("utf-8", "replace")
                line_start = vd.start_point[0] + 1
                line_end = vd.end_point[0] + 1
                # Signature: everything from arrow_function start to body start
                body = value_node.child_by_field_name("body")
                if body:
                    arr_sig = source_bytes[value_node.start_byte: body.start_byte].decode("utf-8", "replace").strip()
                    sig = f"const {name} = {arr_sig}"
                else:
                    sig = f"const {name} = " + _node_text(value_node, source_bytes)[:200]
                doc = _extract_preceding_jsdoc(decl_node.start_point[0], source_lines)
                src = _node_text(vd, source_bytes)
                nodes.append(_make_node(
                    node_id=f"{rel}::{name}",
                    node_type="Function",
                    name=name,
                    file_path=rel,
                    line_start=line_start,
                    line_end=line_end,
                    signature=sig,
                    docstring=doc,
                    source_code=src,
                    route_path=None,
                    file_classification=file_cls,
                    exported=exported,
                ))

        # ── class_declaration ──────────────────────────────────────
        elif ntype == "class_declaration":
            name_node = decl_node.child_by_field_name("name")
            if name_node is None:
                return
            name = name_node.text.decode("utf-8", "replace")
            line_start = decl_node.start_point[0] + 1
            line_end = decl_node.end_point[0] + 1
            sig = _get_signature(decl_node, name, source_bytes)
            doc = _extract_preceding_jsdoc(decl_node.start_point[0], source_lines)
            src = _node_text(decl_node, source_bytes)
            nodes.append(_make_node(
                node_id=f"{rel}::{name}",
                node_type="Class",
                name=name,
                file_path=rel,
                line_start=line_start,
                line_end=line_end,
                signature=sig,
                docstring=doc,
                source_code=src,
                route_path=None,
                file_classification=file_cls,
                exported=exported,
            ))
            # Extract methods from class body
            body = decl_node.child_by_field_name("body")
            if body:
                for method in body.named_children:
                    if method.type not in ("method_definition", "public_field_definition"):
                        continue
                    if method.type != "method_definition":
                        continue
                    mname_node = method.child_by_field_name("name")
                    if mname_node is None:
                        continue
                    mname = mname_node.text.decode("utf-8", "replace")
                    # Skip private, constructor-like, or framework lifecycle names if needed
                    mline_start = method.start_point[0] + 1
                    mline_end = method.end_point[0] + 1
                    msig = _get_signature(method, mname, source_bytes)
                    msrc = _node_text(method, source_bytes)
                    nodes.append(_make_node(
                        node_id=f"{rel}::{name}.{mname}",
                        node_type="Method",
                        name=mname,
                        file_path=rel,
                        line_start=mline_start,
                        line_end=mline_end,
                        signature=msig,
                        docstring=None,
                        source_code=msrc,
                        route_path=None,
                        file_classification=file_cls,
                        exported=False,
                    ))

        # ── interface_declaration ──────────────────────────────────
        elif ntype == "interface_declaration":
            name_node = decl_node.child_by_field_name("name")
            if name_node is None:
                return
            name = name_node.text.decode("utf-8", "replace")
            sig = f"interface {name}"
            ext_clause = None
            for c in decl_node.named_children:
                if c.type == "extends_type_clause":
                    ext_clause = c
                    break
            if ext_clause:
                sig += " extends " + ", ".join(
                    c.text.decode("utf-8", "replace")
                    for c in ext_clause.named_children
                    if c.type == "type_identifier"
                )
            doc = _extract_preceding_jsdoc(decl_node.start_point[0], source_lines)
            nodes.append(_make_node(
                node_id=f"{rel}::{name}",
                node_type="Interface",
                name=name,
                file_path=rel,
                line_start=decl_node.start_point[0] + 1,
                line_end=decl_node.end_point[0] + 1,
                signature=sig,
                docstring=doc,
                source_code=_node_text(decl_node, source_bytes),
                route_path=None,
                file_classification=file_cls,
                exported=exported,
            ))

        # ── type_alias_declaration ─────────────────────────────────
        elif ntype == "type_alias_declaration":
            name_node = decl_node.child_by_field_name("name")
            if name_node is None:
                return
            name = name_node.text.decode("utf-8", "replace")
            sig = f"type {name} = " + _node_text(decl_node.child_by_field_name("value") or decl_node, source_bytes)[:150]
            doc = _extract_preceding_jsdoc(decl_node.start_point[0], source_lines)
            nodes.append(_make_node(
                node_id=f"{rel}::{name}",
                node_type="TypeAlias",
                name=name,
                file_path=rel,
                line_start=decl_node.start_point[0] + 1,
                line_end=decl_node.end_point[0] + 1,
                signature=sig,
                docstring=doc,
                source_code=_node_text(decl_node, source_bytes),
                route_path=None,
                file_classification=file_cls,
                exported=exported,
            ))

        # ── enum_declaration ───────────────────────────────────────
        elif ntype == "enum_declaration":
            name_node = decl_node.child_by_field_name("name")
            if name_node is None:
                return
            name = name_node.text.decode("utf-8", "replace")
            doc = _extract_preceding_jsdoc(decl_node.start_point[0], source_lines)
            nodes.append(_make_node(
                node_id=f"{rel}::{name}",
                node_type="Enum",
                name=name,
                file_path=rel,
                line_start=decl_node.start_point[0] + 1,
                line_end=decl_node.end_point[0] + 1,
                signature=f"enum {name}",
                docstring=doc,
                source_code=_node_text(decl_node, source_bytes),
                route_path=None,
                file_classification=file_cls,
                exported=exported,
            ))

    # Walk top-level statements
    for top_node in root.named_children:
        if top_node.type == "comment":
            continue
        if top_node.type == "export_statement":
            inner_children = [
                c for c in top_node.named_children
                if c.type not in ("comment",)
            ]
            if not inner_children:
                continue
            inner = inner_children[0]
            # Re-exports (export { X } from '...') have export_clause as first child
            if inner.type in ("export_clause", "identifier"):
                continue
            _process_decl(inner, exported=True)
        else:
            _process_decl(top_node, exported=False)

    return nodes


# ── Pass 2: Extract edges from a single file ───────────────────────────

def _is_builtin_call(fn_name: str) -> bool:
    root = fn_name.split(".")[0]
    return root in BUILTIN_PATTERNS


def _get_callee_name(call_node) -> str | None:
    """Extract the function name from a call_expression node."""
    callee = call_node.named_children[0] if call_node.named_children else None
    if callee is None:
        return None
    if callee.type == "identifier":
        return callee.text.decode("utf-8", "replace")
    if callee.type == "member_expression":
        # obj.method → return "obj.method" for builtin check, "method" for resolution
        return callee.text.decode("utf-8", "replace")
    return None


def _extract_edges_from_file(
    file_path: Path,
    repo_root: Path,
    source_bytes: bytes,
    known_node_ids: set[str],
    name_to_node_ids: dict[str, list[str]],
    pkg_deps: set[str],
    alias_map: dict[str, str],
) -> list[tuple[str, str, str]]:
    """
    Extract all (source_id, target_id, edge_type) triples for one file.
    Returns only edges where both endpoints exist in known_node_ids,
    except for DEPENDS_ON_EXTERNAL which targets ExternalPackage nodes.
    """
    rel = _rel_path(file_path, repo_root)
    file_node_id = rel

    parser = _get_parser(file_path)
    tree = parser.parse(source_bytes)
    root = tree.root_node

    edges: list[tuple[str, str, str]] = []

    # ── Build import_map: local_name → resolved_node_id ────────────
    import_map: dict[str, str] = {}      # local_name → node_id
    file_import_map: dict[str, str] = {} # local_name → resolved_file_rel_path

    for top in root.named_children:
        # Direct import statements
        if top.type == "import_statement":
            _process_import(
                top, file_path, repo_root, rel, alias_map, pkg_deps,
                known_node_ids, import_map, file_import_map, edges,
            )
        # export { X } from './module'  (re-export)
        elif top.type == "export_statement":
            for c in top.named_children:
                if c.type == "export_clause":
                    # Check if there is a 'from' source
                    src_str = _get_import_source(top)
                    if src_str:
                        resolved = _resolve_import_path(src_str, file_path, repo_root, alias_map)
                        if resolved and resolved in known_node_ids:
                            edges.append((file_node_id, resolved, "IMPORTS"))
                    break

    # ── Walk top-level declarations for structural edges ────────────
    for top in root.named_children:
        exported = False
        decl = top
        if top.type == "export_statement":
            inner = [c for c in top.named_children if c.type not in ("comment", "export_clause", "identifier")]
            if not inner:
                continue
            decl = inner[0]
            exported = True

        _extract_decl_edges(
            decl, rel, file_node_id, known_node_ids, name_to_node_ids,
            import_map, file_import_map, edges,
        )

    return edges


def _process_import(
    import_node,
    file_path: Path,
    repo_root: Path,
    rel: str,
    alias_map: dict[str, str],
    pkg_deps: set[str],
    known_node_ids: set[str],
    import_map: dict[str, str],
    file_import_map: dict[str, str],
    edges: list[tuple[str, str, str]],
) -> None:
    """Process one import_statement: emit IMPORTS or DEPENDS_ON_EXTERNAL edge and build import_map."""
    src_str = _get_import_source(import_node)
    if src_str is None:
        return

    # Determine if relative/aliased or external
    is_relative = src_str.startswith(".") or any(
        src_str.startswith(prefix) for prefix in alias_map
    )

    if is_relative:
        resolved = _resolve_import_path(src_str, file_path, repo_root, alias_map)
        if resolved and resolved in known_node_ids:
            edges.append((rel, resolved, "IMPORTS"))
            # Build import_map for CALLS resolution
            clause = import_node.named_children[0] if import_node.named_children else None
            if clause and clause.type == "import_clause":
                _build_import_map_from_clause(clause, resolved, known_node_ids, import_map, file_import_map)
    else:
        # External package
        # Normalize: get the top-level package name ("@scope/pkg" → "@scope/pkg", "pkg/sub" → "pkg")
        pkg_name = src_str.split("/")[0]
        if src_str.startswith("@"):
            pkg_name = "/".join(src_str.split("/")[:2])
        if pkg_name in pkg_deps:
            ext_id = f"__external__::{pkg_name}"
            if ext_id in known_node_ids:
                edges.append((rel, ext_id, "DEPENDS_ON_EXTERNAL"))


def _build_import_map_from_clause(
    clause_node,
    resolved_file: str,
    known_node_ids: set[str],
    import_map: dict[str, str],
    file_import_map: dict[str, str],
) -> None:
    """Populate import_map from an import_clause node."""
    for child in clause_node.named_children:
        if child.type == "named_imports":
            # import { foo, bar as baz }
            for spec in child.named_children:
                if spec.type == "import_specifier":
                    spec_children = [c for c in spec.named_children if c.type == "identifier"]
                    if not spec_children:
                        continue
                    # local name is the last identifier (alias if present)
                    local_name = spec_children[-1].text.decode("utf-8", "replace")
                    # original name is the first identifier
                    orig_name = spec_children[0].text.decode("utf-8", "replace")
                    target_id = f"{resolved_file}::{orig_name}"
                    if target_id in known_node_ids:
                        import_map[local_name] = target_id
                    file_import_map[local_name] = resolved_file

        elif child.type == "namespace_import":
            # import * as Ns
            idents = [c for c in child.named_children if c.type == "identifier"]
            if idents:
                local_name = idents[-1].text.decode("utf-8", "replace")
                file_import_map[local_name] = resolved_file

        elif child.type == "identifier":
            # import Default from './module'  → default export
            local_name = child.text.decode("utf-8", "replace")
            # Try "resolved_file::local_name" as the default export node
            for suffix in ("", f"::{local_name}", "::default"):
                candidate = resolved_file + suffix if suffix else resolved_file
                if candidate in known_node_ids:
                    import_map[local_name] = candidate
                    break
            file_import_map[local_name] = resolved_file


def _resolve_call_target(
    call_name: str,
    current_file_rel: str,
    known_node_ids: set[str],
    import_map: dict[str, str],
    name_to_node_ids: dict[str, list[str]],
) -> str | None:
    """
    Resolve a call expression name to a node_id.
    Order: import_map → same-file → global name lookup.
    """
    if _is_builtin_call(call_name):
        return None

    # Handle member_expression: "obj.method"
    if "." in call_name:
        parts = call_name.split(".")
        obj, method = parts[0], parts[-1]
        # Try obj as namespace import → file path → method
        # This handles cases like Utils.someFunc()
        # Skip for now if obj is a known builtin
        if _is_builtin_call(obj):
            return None
        # Try obj as imported object
        if obj in import_map:
            # obj maps to a node_id; try obj_file::method
            obj_node = import_map[obj]
            method_candidate = obj_node.rsplit("::", 1)[0] + f"::{method}"
            if method_candidate in known_node_ids:
                return method_candidate
        return None

    # Step 1: import_map lookup
    if call_name in import_map:
        return import_map[call_name]

    # Step 2: same-file lookup
    same_file_id = f"{current_file_rel}::{call_name}"
    if same_file_id in known_node_ids:
        return same_file_id

    # Step 3: global name lookup (ambiguous — skip to avoid false positives)
    return None


def _extract_decl_edges(
    decl_node,
    rel: str,
    file_node_id: str,
    known_node_ids: set[str],
    name_to_node_ids: dict[str, list[str]],
    import_map: dict[str, str],
    file_import_map: dict[str, str],
    edges: list[tuple[str, str, str]],
) -> None:
    """Extract INHERITS, IMPLEMENTS, DEFINES_METHOD, CALLS, TYPED_BY, RENDERS from one declaration."""
    ntype = decl_node.type

    # ── class_declaration ──────────────────────────────────────────
    if ntype == "class_declaration":
        name_node = decl_node.child_by_field_name("name")
        if name_node is None:
            return
        class_name = name_node.text.decode("utf-8", "replace")
        class_id = f"{rel}::{class_name}"
        if class_id not in known_node_ids:
            return

        # INHERITS and IMPLEMENTS from class_heritage
        for c in decl_node.named_children:
            if c.type != "class_heritage":
                continue
            for hc in c.named_children:
                if hc.type == "extends_clause":
                    for ident in hc.named_children:
                        if ident.type in ("identifier", "type_identifier"):
                            target_name = ident.text.decode("utf-8", "replace")
                            target_id = import_map.get(target_name) or f"{rel}::{target_name}"
                            if target_id in known_node_ids:
                                edges.append((class_id, target_id, "INHERITS"))
                elif hc.type == "implements_clause":
                    for ident in hc.named_children:
                        if ident.type == "type_identifier":
                            target_name = ident.text.decode("utf-8", "replace")
                            target_id = import_map.get(target_name) or f"{rel}::{target_name}"
                            if target_id in known_node_ids:
                                edges.append((class_id, target_id, "IMPLEMENTS"))

        # DEFINES_METHOD + method-level edges
        body = decl_node.child_by_field_name("body")
        if body:
            for method in body.named_children:
                if method.type != "method_definition":
                    continue
                mname_node = method.child_by_field_name("name")
                if mname_node is None:
                    continue
                mname = mname_node.text.decode("utf-8", "replace")
                method_id = f"{rel}::{class_name}.{mname}"
                if method_id in known_node_ids:
                    edges.append((class_id, method_id, "DEFINES_METHOD"))
                    # TYPED_BY from method parameters and return type
                    _emit_typed_by(
                        method_id, method, known_node_ids, import_map, rel, edges,
                    )
                    # CALLS from method body
                    method_body = method.child_by_field_name("body")
                    if method_body:
                        _emit_calls(
                            method_id, method_body, rel, known_node_ids,
                            import_map, name_to_node_ids, edges,
                        )

    # ── function_declaration ───────────────────────────────────────
    elif ntype == "function_declaration":
        name_node = decl_node.child_by_field_name("name")
        if name_node is None:
            return
        fn_name = name_node.text.decode("utf-8", "replace")
        fn_id = f"{rel}::{fn_name}"
        if fn_id not in known_node_ids:
            return
        _emit_typed_by(fn_id, decl_node, known_node_ids, import_map, rel, edges)
        body = decl_node.child_by_field_name("body")
        if body:
            _emit_calls(fn_id, body, rel, known_node_ids, import_map, name_to_node_ids, edges)
            _emit_renders(fn_id, body, known_node_ids, import_map, rel, edges)

    # ── lexical_declaration (arrow functions) ──────────────────────
    elif ntype == "lexical_declaration":
        for vd in decl_node.named_children:
            if vd.type != "variable_declarator":
                continue
            name_node = vd.child_by_field_name("name")
            value_node = vd.child_by_field_name("value")
            if name_node is None or value_node is None:
                continue
            if value_node.type != "arrow_function":
                continue
            fn_name = name_node.text.decode("utf-8", "replace")
            fn_id = f"{rel}::{fn_name}"
            if fn_id not in known_node_ids:
                continue
            _emit_typed_by(fn_id, value_node, known_node_ids, import_map, rel, edges)
            body = value_node.child_by_field_name("body")
            if body:
                _emit_calls(fn_id, body, rel, known_node_ids, import_map, name_to_node_ids, edges)
                _emit_renders(fn_id, body, known_node_ids, import_map, rel, edges)


def _emit_typed_by(
    source_id: str,
    func_node,
    known_node_ids: set[str],
    import_map: dict[str, str],
    rel: str,
    edges: list[tuple[str, str, str]],
) -> None:
    """Emit TYPED_BY edges from a function/method node's type annotations."""
    params = func_node.child_by_field_name("parameters")
    return_type = func_node.child_by_field_name("return_type")
    type_names = _extract_type_annotations(params, return_type)
    seen: set[str] = set()
    for type_name in type_names:
        if type_name in seen:
            continue
        seen.add(type_name)
        target_id = import_map.get(type_name) or f"{rel}::{type_name}"
        if target_id in known_node_ids:
            edges.append((source_id, target_id, "TYPED_BY"))


def _emit_calls(
    source_id: str,
    body_node,
    rel: str,
    known_node_ids: set[str],
    import_map: dict[str, str],
    name_to_node_ids: dict[str, list[str]],
    edges: list[tuple[str, str, str]],
) -> None:
    """Emit CALLS edges for all call_expressions found in body_node."""
    call_nodes = _find_in_body_no_cross(body_node, {"call_expression"})
    seen: set[str] = set()
    for call in call_nodes:
        call_name = _get_callee_name(call)
        if call_name is None:
            continue
        target_id = _resolve_call_target(
            call_name, rel, known_node_ids, import_map, name_to_node_ids,
        )
        if target_id and target_id != source_id and target_id not in seen:
            seen.add(target_id)
            edges.append((source_id, target_id, "CALLS"))


def _emit_renders(
    source_id: str,
    body_node,
    known_node_ids: set[str],
    import_map: dict[str, str],
    rel: str,
    edges: list[tuple[str, str, str]],
) -> None:
    """Emit RENDERS edges for JSX elements in body_node that reference uppercase components."""
    jsx_nodes = _find_in_body_no_cross(
        body_node,
        {"jsx_element", "jsx_self_closing_element"},
    )
    seen: set[str] = set()
    for jsx in jsx_nodes:
        tag_name = _get_jsx_tag_name(jsx)
        if tag_name is None or not tag_name[0].isupper():
            continue  # HTML element, not React component
        # Resolve to node_id
        target_id = import_map.get(tag_name) or f"{rel}::{tag_name}"
        if target_id in known_node_ids and target_id != source_id and target_id not in seen:
            seen.add(target_id)
            edges.append((source_id, target_id, "RENDERS"))


def _get_jsx_tag_name(jsx_node) -> str | None:
    """Extract the component name from a jsx_element or jsx_self_closing_element."""
    if jsx_node.type == "jsx_self_closing_element":
        # First named child is the tag identifier
        for c in jsx_node.named_children:
            if c.type in ("identifier", "member_expression", "namespace_name"):
                return c.text.decode("utf-8", "replace").split(".")[0]
        return None
    if jsx_node.type == "jsx_element":
        # First child is jsx_opening_element
        for c in jsx_node.named_children:
            if c.type == "jsx_opening_element":
                for gc in c.named_children:
                    if gc.type in ("identifier", "member_expression", "namespace_name"):
                        return gc.text.decode("utf-8", "replace").split(".")[0]
        return None
    return None


# ── SQLite insertion ───────────────────────────────────────────────────

def _insert_nodes(conn: sqlite3.Connection, nodes: list[dict]) -> None:
    conn.executemany(
        """
        INSERT OR IGNORE INTO code_nodes
            (node_id, node_type, name, file_path, line_start, line_end,
             signature, docstring, source_code, route_path,
             file_classification, exported, embed_text)
        VALUES
            (:node_id, :node_type, :name, :file_path, :line_start, :line_end,
             :signature, :docstring, :source_code, :route_path,
             :file_classification, :exported, :embed_text)
        """,
        nodes,
    )
    conn.commit()


def _insert_edges(
    conn: sqlite3.Connection,
    edges: list[tuple[str, str, str]],
    known_node_ids: set[str],
) -> None:
    valid = [
        (src, tgt, etype)
        for src, tgt, etype in edges
        if src in known_node_ids and tgt in known_node_ids
    ]
    # Deduplicate
    unique = list(dict.fromkeys(valid))
    conn.executemany(
        """
        INSERT OR IGNORE INTO structural_edges (source_id, target_id, edge_type)
        VALUES (?, ?, ?)
        """,
        unique,
    )
    conn.commit()


# ── Main entry point ───────────────────────────────────────────────────

def index_repository(repo_root: str, conn: sqlite3.Connection) -> None:
    """
    Two-pass AST indexer.

    Pass 1: Parse every .ts/.tsx file, extract code_nodes, build
            known_node_ids registry and name_to_node_ids lookup.
    Pass 2: Parse every file again, extract structural_edges using
            the registry to validate targets before insertion.
    """
    root = Path(repo_root).resolve()

    pkg_deps = _load_package_deps(root)
    alias_map = _load_alias_map(root)
    ts_files = _find_ts_files(root)

    # ── Create ExternalPackage nodes for known dependencies ─────────
    ext_nodes: list[dict] = []
    for pkg in sorted(pkg_deps):
        ext_nodes.append(_make_node(
            node_id=f"__external__::{pkg}",
            node_type="ExternalPackage",
            name=pkg,
            file_path=None,
            line_start=None,
            line_end=None,
            signature=None,
            docstring=None,
            source_code=None,
            route_path=None,
            file_classification=None,
            exported=False,
        ))

    # ── Pass 1: Extract all nodes ───────────────────────────────────
    all_nodes: list[dict] = list(ext_nodes)
    known_node_ids: set[str] = {n["node_id"] for n in ext_nodes}
    name_to_node_ids: dict[str, list[str]] = {}

    parse_errors: list[str] = []
    for fp in ts_files:
        try:
            source_bytes = fp.read_bytes()
            file_nodes = _extract_nodes_from_file(fp, root, source_bytes)
            for n in file_nodes:
                if n["node_id"] not in known_node_ids:
                    all_nodes.append(n)
                    known_node_ids.add(n["node_id"])
                    name = n["name"]
                    name_to_node_ids.setdefault(name, []).append(n["node_id"])
        except Exception as exc:
            parse_errors.append(f"{fp}: {exc}")

    _insert_nodes(conn, all_nodes)

    # ── Pass 2: Extract all edges ───────────────────────────────────
    all_edges: list[tuple[str, str, str]] = []
    for fp in ts_files:
        try:
            source_bytes = fp.read_bytes()
            file_edges = _extract_edges_from_file(
                fp, root, source_bytes, known_node_ids,
                name_to_node_ids, pkg_deps, alias_map,
            )
            all_edges.extend(file_edges)
        except Exception as exc:
            parse_errors.append(f"{fp} (edges): {exc}")

    _insert_edges(conn, all_edges, known_node_ids)

    # ── Update index_metadata ───────────────────────────────────────
    from datetime import datetime, timezone
    meta = {
        "repo_path": str(root),
        "last_indexed_at": datetime.now(timezone.utc).isoformat(),
        "total_code_nodes": str(sum(1 for n in all_nodes if n["node_type"] != "ExternalPackage")),
        "total_doc_chunks": "0",
    }
    conn.executemany(
        "INSERT OR REPLACE INTO index_metadata (key, value) VALUES (?, ?)",
        list(meta.items()),
    )
    conn.commit()

    if parse_errors:
        from loguru import logger
        for err in parse_errors[:20]:
            logger.warning("Parse error: {}", err)
