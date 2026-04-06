#!/usr/bin/env python3
"""
Omninet SDK Codegen — generates TypeScript types and typed op wrappers
from the Rust crate sources and C FFI header.

Three passes:
  1. Parse all Rust structs/enums with Serialize/Deserialize → TypeScript interfaces
  2A. Parse the C header (divinity_ffi.h) for 100% op discovery + C return types
  2B. Parse Rust FFI source files for rich return type tracing (enrichment)
  3. Merge: C header provides completeness, Rust tracing provides type richness

Usage:
  python3 scripts/generate.py
  python3 scripts/generate.py --omninet-path /path/to/Omninet
"""

import re
import os
import sys
import json
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

# ── Config ─────────────────────────────────────────────────────

OMNINET_PATH = Path(os.environ.get(
    "OMNINET_PATH",
    Path(__file__).resolve().parent.parent.parent.parent / "Omninet"
))
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "src" / "generated"

# Crate source directories (relative to OMNINET_PATH)
CRATE_DIRS = [
    "X/src", "Ideas/src", "Equipment/src", "Sentinal/src", "Vault/src",
    "Hall/src", "Crown/src", "Globe/src", "Lingo/src", "Polity/src",
    "Kingdom/src", "Fortune/src", "Bulwark/src", "Jail/src", "Regalia/src",
    "Magic/src", "Nexus/src", "Advisor/src", "Yoke/src", "Zeitgeist/src",
    "Oracle/src", "Quest/src", "World/Digital/Omnibus/src",
    "World/Digital/Tower/src", "World/Digital/MagicalIndex/src",
    "World/Physical/src", "Undercroft/src", "Undercroft/AppCatalog/src",
    "Undercroft/DeviceManager/src", "_Codecs/src",
]

FFI_DIR = "Divinity/ffi/src"
C_HEADER = "Divinity/Apple/Sources/COmnideaFFI/include/divinity_ffi.h"

# ── Rust → TypeScript type mapping ─────────────────────────────

PRIMITIVE_MAP = {
    "String": "string",
    "&str": "string",
    "str": "string",
    "bool": "boolean",
    "i8": "number", "i16": "number", "i32": "number", "i64": "number",
    "u8": "number", "u16": "number", "u32": "number", "u64": "number",
    "usize": "number", "isize": "number",
    "f32": "number", "f64": "number",
    "Uuid": "string",
    "DateTime<Utc>": "string",
    "NaiveDate": "string",
    "NaiveDateTime": "string",
    "Duration": "number",
    "PathBuf": "string",
    "Url": "string",
}

# Types that need manual TypeScript definitions (custom serde)
MANUAL_OVERRIDES: dict[str, str] = {
    "AvatarReference": (
        '  | { type: "url"; url: string }\n'
        '  | { type: "asset"; idea_id: string; asset_name: string }\n'
        '  | { type: "data"; data: string; mime_type: string }'
    ),
    # x::Value uses single-key discriminated JSON
    "Value": "Record<string, unknown>",
}

# Ad-hoc JSON shapes from FFI functions (edge cases with serde_json::json!)
ADHOC_SHAPES: dict[str, str] = {
    "crown.keyring_generate_primary": '{ crown_id: string; cpub_hex: string }',
    "crown.derive_blinded_keypair": '{ crown_id: string; cpub_hex: string }',
    "device.manager_list_sync_conflicts": '{ device_crown_id: string; data_type: string; state: unknown }[]',
    "globe.process_forward": '{ action: string; [key: string]: unknown }',
    "oracle.disclosure_tracker_signal_counts": '{ steward: number; architect: number }',
}

# Helper/lifecycle functions to exclude from the SDK (not pipeline ops)
SKIP_FUNCTIONS = {
    'divi_free_string', 'divi_free_bytes', 'divi_last_error',
    'divi_runtime_new', 'divi_runtime_free',
}

# Void functions that are Equipment callback plumbing (orchestrator-internal).
# These register/unregister handlers and manage subscription lifecycle —
# the orchestrator wires these up, programs don't call them directly.
VOID_SKIP_PATTERNS = {
    '_on_event',        # callback registration (globe, omnibus)
    '_register_raw',    # raw phone handler registration
    '_unregister',      # phone handler removal
    '_send_raw',        # raw email send (orchestrator wraps this)
    '_unsubscribe',     # email unsubscribe (orchestrator wraps this)
    '_shutdown_all',    # contacts shutdown (orchestrator lifecycle)
}

# ── Data structures ────────────────────────────────────────────

@dataclass
class RustField:
    name: str
    rust_type: str
    optional: bool = False
    serde_rename: Optional[str] = None
    serde_skip: bool = False
    serde_default: bool = False
    serde_flatten: bool = False

@dataclass
class RustStruct:
    name: str
    fields: list[RustField] = field(default_factory=list)
    serde_rename_all: Optional[str] = None
    doc: str = ""
    source_file: str = ""

@dataclass
class RustEnum:
    name: str
    variants: list[tuple[str, Optional[str]]] = field(default_factory=list)  # (name, data_type or None)
    serde_rename_all: Optional[str] = None
    serde_tag: Optional[str] = None
    serde_content: Optional[str] = None
    serde_untagged: bool = False
    doc: str = ""
    source_file: str = ""

@dataclass
class FfiFunction:
    name: str               # divi_crown_soul_profile
    op_name: str            # crown.soul_profile
    return_category: str    # "json", "string", "i32", "bool", "usize", "ptr", "void"
    return_type: Optional[str] = None  # Rust type name if json, e.g. "Profile"
    doc: str = ""
    source_file: str = ""

# ── Pass 1: Parse Rust types ──────────────────────────────────

def parse_rust_types(omninet: Path) -> tuple[dict[str, RustStruct], dict[str, RustEnum]]:
    """Parse all structs and enums that derive Serialize or Deserialize."""
    structs: dict[str, RustStruct] = {}
    enums: dict[str, RustEnum] = {}

    for crate_dir in CRATE_DIRS:
        src_path = omninet / crate_dir
        if not src_path.exists():
            continue
        # Derive crate name from directory
        crate_name = crate_dir.split('/')[0]
        for rs_file in src_path.rglob("*.rs"):
            content = rs_file.read_text(errors="replace")
            file_structs, file_enums = parse_file_types(content, str(rs_file.relative_to(omninet)))
            # Handle name collisions by prefixing with crate name
            for name, s in file_structs.items():
                if name in structs:
                    # Rename both: prefix existing with its crate, new with this crate
                    existing = structs.pop(name)
                    existing_crate = existing.source_file.split('/')[0]
                    structs[f"{existing_crate}{name}"] = existing
                    structs[f"{crate_name}{name}"] = s
                else:
                    structs[name] = s
            for name, e in file_enums.items():
                if name in enums:
                    existing = enums.pop(name)
                    existing_crate = existing.source_file.split('/')[0]
                    enums[f"{existing_crate}{name}"] = existing
                    enums[f"{crate_name}{name}"] = e
                else:
                    enums[name] = e

    return structs, enums


def parse_file_types(content: str, source: str) -> tuple[dict[str, RustStruct], dict[str, RustEnum]]:
    """Parse structs and enums from a single Rust file."""
    structs: dict[str, RustStruct] = {}
    enums: dict[str, RustEnum] = {}
    lines = content.split('\n')
    i = 0

    while i < len(lines):
        line = lines[i].strip()

        # Look for derive blocks containing Serialize or Deserialize
        if '#[derive(' in line or line.startswith('#[derive('):
            derive_block = line
            # Multi-line derive
            while ')' not in derive_block and i + 1 < len(lines):
                i += 1
                derive_block += ' ' + lines[i].strip()

            if 'Serialize' not in derive_block and 'Deserialize' not in derive_block:
                i += 1
                continue

            # Collect serde attributes between derive and struct/enum
            serde_attrs: dict[str, str] = {}
            j = i + 1
            while j < len(lines):
                attr_line = lines[j].strip()
                if attr_line.startswith('#[serde('):
                    serde_attrs.update(parse_serde_attr(attr_line))
                    j += 1
                elif attr_line.startswith('#[') or attr_line.startswith('///'):
                    j += 1
                else:
                    break

            decl_line = lines[j].strip() if j < len(lines) else ""

            # Parse struct
            struct_match = re.match(r'pub\s+struct\s+(\w+)', decl_line)
            if struct_match:
                name = struct_match.group(1)
                if name in MANUAL_OVERRIDES:
                    i = j + 1
                    continue
                # Find the struct body
                body_start = j
                while body_start < len(lines) and '{' not in lines[body_start]:
                    body_start += 1
                if body_start < len(lines):
                    body, body_end = extract_braced_block(lines, body_start)
                    fields = parse_struct_fields(body)
                    structs[name] = RustStruct(
                        name=name,
                        fields=fields,
                        serde_rename_all=serde_attrs.get('rename_all'),
                        source_file=source,
                    )
                    i = body_end + 1
                    continue

            # Parse enum
            enum_match = re.match(r'pub\s+enum\s+(\w+)', decl_line)
            if enum_match:
                name = enum_match.group(1)
                if name in MANUAL_OVERRIDES:
                    i = j + 1
                    continue
                body_start = j
                while body_start < len(lines) and '{' not in lines[body_start]:
                    body_start += 1
                if body_start < len(lines):
                    body, body_end = extract_braced_block(lines, body_start)
                    variants = parse_enum_variants(body)
                    enums[name] = RustEnum(
                        name=name,
                        variants=variants,
                        serde_rename_all=serde_attrs.get('rename_all'),
                        serde_tag=serde_attrs.get('tag'),
                        serde_content=serde_attrs.get('content'),
                        serde_untagged='untagged' in serde_attrs,
                        source_file=source,
                    )
                    i = body_end + 1
                    continue

        i += 1

    return structs, enums


def parse_serde_attr(line: str) -> dict[str, str]:
    """Parse #[serde(...)] attributes into a dict."""
    attrs = {}
    match = re.search(r'#\[serde\((.+)\)\]', line)
    if not match:
        return attrs
    inner = match.group(1)

    # Handle simple flags
    if inner.strip() == 'untagged':
        attrs['untagged'] = 'true'
        return attrs

    # Handle key = "value" pairs
    for pair in re.finditer(r'(\w+)\s*=\s*"([^"]*)"', inner):
        attrs[pair.group(1)] = pair.group(2)

    # Handle bare flags
    for flag in re.finditer(r'\b(untagged|default|flatten|skip)\b', inner):
        attrs[flag.group(1)] = 'true'

    return attrs


def extract_braced_block(lines: list[str], start: int) -> tuple[str, int]:
    """Extract content between { and matching }."""
    depth = 0
    body_lines = []
    i = start
    started = False
    while i < len(lines):
        line = lines[i]
        for ch in line:
            if ch == '{':
                depth += 1
                started = True
            elif ch == '}':
                depth -= 1
                if depth == 0 and started:
                    return '\n'.join(body_lines), i
        if started and depth > 0:
            body_lines.append(line)
        i += 1
    return '\n'.join(body_lines), i


def parse_struct_fields(body: str) -> list[RustField]:
    """Parse struct fields from the body text."""
    fields = []
    seen_names: set[str] = set()
    lines = body.split('\n')
    serde_field_attrs: dict[str, str] = {}

    for line in lines:
        stripped = line.strip()

        # Collect field-level serde attributes
        if stripped.startswith('#[serde('):
            serde_field_attrs.update(parse_serde_attr(stripped))
            continue

        # Skip doc comments and other attributes
        if stripped.startswith('//') or stripped.startswith('#['):
            continue

        # Match field: [pub] name: Type,  (serde serializes all fields, not just pub)
        field_match = re.match(r'(?:pub(?:\(crate\))?\s+)?(\w+)\s*:\s*(.+?)\s*,?\s*$', stripped)
        if field_match:
            name = field_match.group(1)
            rust_type = field_match.group(2).strip().rstrip(',')

            if serde_field_attrs.get('skip') == 'true':
                serde_field_attrs = {}
                continue

            # Skip duplicate fields (can happen with enum struct variants parsed as struct)
            if name in seen_names:
                serde_field_attrs = {}
                continue
            seen_names.add(name)

            fields.append(RustField(
                name=name,
                rust_type=rust_type,
                optional='Option<' in rust_type,
                serde_rename=serde_field_attrs.get('rename'),
                serde_skip=serde_field_attrs.get('skip') == 'true',
                serde_default=serde_field_attrs.get('default') == 'true',
                serde_flatten=serde_field_attrs.get('flatten') == 'true',
            ))
            serde_field_attrs = {}

    return fields


def parse_enum_variants(body: str) -> list[tuple[str, Optional[str]]]:
    """Parse enum variants. Returns list of (name, data_type_or_None)."""
    variants = []
    lines = body.split('\n')

    for line in lines:
        stripped = line.strip()
        if stripped.startswith('//') or stripped.startswith('#[') or not stripped:
            continue

        # Variant with data: Name(Type) or Name { ... }
        data_match = re.match(r'(\w+)\s*\((.+?)\)\s*,?', stripped)
        if data_match:
            variants.append((data_match.group(1), data_match.group(2).strip()))
            continue

        struct_match = re.match(r'(\w+)\s*\{', stripped)
        if struct_match:
            variants.append((struct_match.group(1), "struct"))
            continue

        # Simple variant: Name, or Name = N,
        simple_match = re.match(r'(\w+)\s*(?:=\s*\w+)?\s*,?\s*$', stripped)
        if simple_match:
            variants.append((simple_match.group(1), None))
            continue

    return variants


# ── Pass 2A: Parse C header for complete op discovery ─────────

def classify_c_return(c_ret: str) -> str:
    """Classify a C return type string into a category."""
    c_ret = c_ret.strip()
    if c_ret == 'bool':
        return "bool"
    if c_ret in ('int32_t', 'int64_t'):
        return "i32"
    if c_ret in ('uintptr_t', 'uint32_t', 'uint16_t', 'uint64_t'):
        return "usize"
    if c_ret == 'double':
        return "double"
    if c_ret == 'void':
        return "void"
    if 'char' in c_ret and '*' in c_ret:
        return "json"  # char * returns are JSON or string
    if 'uint8_t' in c_ret and '*' in c_ret:
        return "bytes"  # raw byte returns
    if '*' in c_ret:
        return "ptr"   # any other pointer return is opaque constructor
    return "void"


def parse_c_header(omninet: Path) -> list[FfiFunction]:
    """Parse divinity_ffi.h for ALL divi_* function declarations.

    This is the source of truth — same file the Zig orchestrator reads
    at comptime for op discovery. Guarantees 100% coverage.
    """
    header_path = omninet / C_HEADER
    if not header_path.exists():
        print(f"  WARNING: C header not found at {header_path}", file=sys.stderr)
        return []

    content = header_path.read_text(errors="replace")
    functions: list[FfiFunction] = []

    # Match C function declarations. The header is cbindgen-generated, so the
    # format is very consistent. Declarations may span multiple lines when
    # parameter lists are long, but the return type + function name are always
    # on the first line of the declaration.
    #
    # Return types:
    #   char * / const char *         → JSON or string
    #   uint8_t *                     → raw bytes
    #   struct T * / T *              → opaque pointer (constructor)
    #   int32_t / int64_t             → integer
    #   uint16_t / uint32_t / uint64_t / uintptr_t → unsigned integer
    #   bool                          → boolean
    #   double                        → float
    #   void                          → no return
    pattern = re.compile(
        r'^((?:const\s+)?char\s*\*'              # char * / const char *
        r'|(?:const\s+)?uint8_t\s*\*'            # uint8_t * (raw bytes)
        r'|(?:const\s+)?(?:struct\s+)?\w+\s*\*'  # struct T * or T * (opaque ptrs)
        r'|int32_t|int64_t'                       # signed integers
        r'|uint16_t|uint32_t|uint64_t|uintptr_t'  # unsigned integers
        r'|bool'                                   # boolean
        r'|double'                                 # float
        r'|void'                                   # void
        r')\s*(divi_\w+)\s*\(',                    # function name
        re.MULTILINE
    )

    for m in pattern.finditer(content):
        c_ret = m.group(1).strip()
        fn_name = m.group(2)

        if fn_name in SKIP_FUNCTIONS:
            continue

        category = classify_c_return(c_ret)
        op_name = fn_name_to_op(fn_name)

        functions.append(FfiFunction(
            name=fn_name,
            op_name=op_name,
            return_category=category,
            return_type=None,  # Will be enriched by Rust tracing
            source_file="divinity_ffi.h",
        ))

    return functions


# ── Pass 2B: Rust FFI source enrichment ───────────────────────

def build_rust_type_hints(omninet: Path) -> dict[str, tuple[str, Optional[str]]]:
    """Parse Rust FFI source files to build a type hint lookup table.

    Returns dict of fn_name → (return_category, traced_rust_type).
    The traced_rust_type is the specific Rust struct/enum name that the
    function serializes to JSON, determined by reading the function body.
    """
    ffi_path = omninet / FFI_DIR
    hints: dict[str, tuple[str, Optional[str]]] = {}

    for rs_file in sorted(ffi_path.glob("*_ffi.rs")):
        content = rs_file.read_text(errors="replace")
        file_hints = parse_ffi_file_for_hints(content)
        hints.update(file_hints)

    return hints


def parse_ffi_file_for_hints(content: str) -> dict[str, tuple[str, Optional[str]]]:
    """Parse a single FFI Rust file for return type hints."""
    hints: dict[str, tuple[str, Optional[str]]] = {}
    lines = content.split('\n')
    i = 0

    while i < len(lines):
        line = lines[i].strip()

        # Look for #[unsafe(no_mangle)] or #[no_mangle]
        if 'no_mangle' in line:
            # Collect the function signature (may span multiple lines)
            j = i + 1
            sig_lines = []
            while j < len(lines):
                sig_line = lines[j].strip()
                sig_lines.append(sig_line)
                if '{' in sig_line or sig_line.endswith('{'):
                    break
                j += 1

            sig = ' '.join(sig_lines)

            # Extract function name
            fn_match = re.search(r'fn\s+(divi_\w+)', sig)
            if fn_match:
                fn_name = fn_match.group(1)

                if fn_name in SKIP_FUNCTIONS:
                    i = j + 1
                    continue

                # Only trace char* returns — those are the ones that might
                # have rich type info in the body
                ret_match = re.search(r'->\s*(.+?)(?:\s*\{|$)', sig)
                if ret_match:
                    ret_type = ret_match.group(1).strip()
                    if '*mut c_char' in ret_type or '*mut std::ffi::c_char' in ret_type:
                        # Read the function body to trace the return type
                        body_start = j
                        body_end = find_matching_brace(lines, body_start)
                        body = '\n'.join(lines[body_start:body_end + 1])

                        category, traced = trace_return_from_body(body)
                        hints[fn_name] = (category, traced)

            i = j + 1
            continue

        i += 1

    return hints


def trace_return_from_body(body: str) -> tuple[str, Optional[str]]:
    """Analyze a function body to determine if it returns JSON or string,
    and try to trace the specific Rust type being serialized."""

    # Check for json_to_c(&variable)
    json_match = re.findall(r'json_to_c\(&(\w+)\)', body)
    if json_match:
        var_name = json_match[-1]  # Take the last one (usually in Ok arm)

        # Check if it's a serde_json::json! ad-hoc construction
        if re.search(rf'let\s+{var_name}\s*=\s*serde_json::json!', body):
            return ("json", None)  # Ad-hoc, will use ADHOC_SHAPES

        # Try to trace the variable type from the body
        traced = trace_variable_type(var_name, body)
        if traced:
            return ("json", traced)
        return ("json", None)

    # Check for string_to_c
    if 'string_to_c' in body:
        return ("string", None)

    return ("json", None)


def trace_variable_type(var_name: str, body: str) -> Optional[str]:
    """Try to determine the Rust type of a variable from the function body."""

    # Check for explicit type annotation: let var: Type = ...
    type_ann = re.search(rf'let\s+{var_name}\s*:\s*(\w+(?:<[^>]+>)?)', body)
    if type_ann:
        t = type_ann.group(1)
        t = re.sub(r'^Vec<(.+)>$', r'\1[]', t)
        return t.split('<')[0]

    # Check for: let var = Type::method(...)
    constructor = re.search(rf'let\s+{var_name}\s*=\s*(\w+)::\w+', body)
    if constructor:
        return constructor.group(1)

    # Check for: Ok(var) in a match on a method call
    ok_pattern = re.search(
        rf'(\w+)\.(\w+)\([^)]*\)\s*\{{\s*Ok\(\s*{var_name}\s*\)',
        body.replace('\n', ' ')
    )
    if ok_pattern:
        method = ok_pattern.group(2)
        return method_to_type_hint(method)

    # Look for: match expr { Ok(var) => ...
    match_expr = re.search(
        rf'match\s+(.+?)\s*\{{\s*(?:.*?)Ok\(\s*{var_name}\s*\)',
        body.replace('\n', ' '),
        re.DOTALL
    )
    if match_expr:
        expr = match_expr.group(1).strip()
        method_call = re.search(r'\.(\w+)\s*\(', expr)
        if method_call:
            return method_to_type_hint(method_call.group(1))

    # Variable name hints
    name_hints: dict[str, Optional[str]] = {
        "sig": "Signature",
        "signature": "Signature",
        "profile": "Profile",
        "proof": "BlindingProof",
        "prefs": "Preferences",
        "preferences": "Preferences",
        "soul": "SoulStorage",
        "graph": "SocialGraph",
        "offer": "SyncOffer",
        "accept": "SyncAccept",
        "payload": "SyncPayload",
        "announcement": "RotationAnnouncement",
        "charter": "Charter",
        "event": "OmniEvent",
        "names": None,
    }
    if var_name in name_hints:
        return name_hints[var_name]

    return None


def method_to_type_hint(method: str) -> Optional[str]:
    """Guess the return type from a method name."""
    hints: dict[str, Optional[str]] = {
        "sign": "Signature",
        "sign_blinded": "Signature",
        "profile": "Profile",
        "preferences": "Preferences",
        "social_graph": "SocialGraph",
        "create_sync_offer": "SyncOffer",
        "verify_sync_accept": None,
        "prepare_sync_payload": "SyncPayload",
        "rotate_primary": "RotationAnnouncement",
        "anomaly_check": None,
        "encrypt": None,
        "decrypt": None,
    }
    return hints.get(method)


def find_matching_brace(lines: list[str], start: int) -> int:
    """Find the line containing the matching closing brace."""
    depth = 0
    for i in range(start, len(lines)):
        for ch in lines[i]:
            if ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    return i
    return len(lines) - 1


# ── Pass 2 Merge: C header + Rust enrichment ─────────────────

def fn_name_to_op(fn_name: str) -> str:
    """Convert divi_crown_soul_profile → crown.soul_profile."""
    without_prefix = fn_name.removeprefix("divi_")
    idx = without_prefix.find('_')
    if idx == -1:
        return without_prefix
    return without_prefix[:idx] + '.' + without_prefix[idx + 1:]


def merge_ops(c_functions: list[FfiFunction],
              rust_hints: dict[str, tuple[str, Optional[str]]]) -> list[FfiFunction]:
    """Merge C header discovery with Rust type enrichment.

    C header provides: complete list + C return type category
    Rust hints provide: refined category (json vs string) + traced Rust type name

    For each function:
    - If Rust tracing found a specific type → use it
    - If Rust tracing refined json→string → use that category
    - Otherwise → keep the C header category with no specific type
    """
    for fn in c_functions:
        if fn.name in rust_hints:
            rust_cat, rust_type = rust_hints[fn.name]
            # Rust body tracing is more specific for char* returns
            if fn.return_category == "json":
                fn.return_category = rust_cat  # might refine to "string"
                fn.return_type = rust_type      # might be a specific Rust type
    return c_functions


# ── TypeScript generation ─────────────────────────────────────

def rust_type_to_ts(rust_type: str, known_types: set[str]) -> str:
    """Convert a Rust type to TypeScript."""
    t = rust_type.strip()

    # Direct primitive mapping
    if t in PRIMITIVE_MAP:
        return PRIMITIVE_MAP[t]

    # Fixed-size arrays [T; N] → T[]
    arr_match = re.match(r'\[(\w+);\s*\d+\]', t)
    if arr_match:
        inner = arr_match.group(1)
        if inner == 'u8':
            return "string"
        return f"{rust_type_to_ts(inner, known_types)}[]"

    # Option<T> → T | null
    opt_match = re.match(r'Option<(.+)>', t)
    if opt_match:
        inner = rust_type_to_ts(opt_match.group(1), known_types)
        return f"{inner} | null"

    # Vec<T> → T[]
    vec_match = re.match(r'Vec<(.+)>', t)
    if vec_match:
        inner = vec_match.group(1).strip()
        if inner == 'u8':
            return "string"
        ts_inner = rust_type_to_ts(inner, known_types)
        return f"{ts_inner}[]"

    # HashMap<K, V> → Record<K, V>
    map_match = re.match(r'HashMap<(.+),\s*(.+)>', t)
    if map_match:
        k = rust_type_to_ts(map_match.group(1).strip(), known_types)
        v = rust_type_to_ts(map_match.group(2).strip(), known_types)
        if k not in ('string', 'number'):
            k = 'string'
        return f"Record<{k}, {v}>"

    # HashSet<T> → T[]
    set_match = re.match(r'HashSet<(.+)>', t)
    if set_match:
        inner = rust_type_to_ts(set_match.group(1).strip(), known_types)
        return f"{inner}[]"

    # BTreeMap<K, V> → Record<K, V>
    btree_match = re.match(r'BTreeMap<(.+),\s*(.+)>', t)
    if btree_match:
        k = rust_type_to_ts(btree_match.group(1).strip(), known_types)
        v = rust_type_to_ts(btree_match.group(2).strip(), known_types)
        if k not in ('string', 'number'):
            k = 'string'
        return f"Record<{k}, {v}>"

    # Box<T> → T
    box_match = re.match(r'Box<(.+)>', t)
    if box_match:
        return rust_type_to_ts(box_match.group(1).strip(), known_types)

    # Known type (parsed struct/enum)
    base_type = t.split('<')[0].strip()
    if base_type in known_types:
        return base_type

    # Reference types
    if t.startswith('&'):
        return rust_type_to_ts(t.lstrip('& ').lstrip("'_ "), known_types)

    # Fallback
    return "unknown"


def apply_rename(name: str, rename_all: Optional[str]) -> str:
    """Apply serde rename_all to a field or variant name."""
    if not rename_all:
        return name
    if rename_all == "camelCase":
        if '_' in name:
            parts = name.split('_')
            return parts[0] + ''.join(p.capitalize() for p in parts[1:])
        else:
            return name[0].lower() + name[1:] if name else name
    if rename_all == "snake_case":
        return name
    if rename_all == "lowercase":
        return name.lower()
    if rename_all == "UPPERCASE":
        return name.upper()
    if rename_all == "PascalCase":
        return ''.join(p.capitalize() for p in name.split('_'))
    if rename_all == "SCREAMING_SNAKE_CASE":
        return name.upper()
    if rename_all == "kebab-case":
        return name.replace('_', '-')
    return name


def generate_ts_struct(s: RustStruct, known_types: set[str]) -> str:
    """Generate a TypeScript interface from a Rust struct."""
    lines = [f"export interface {s.name} {{"]
    for f in s.fields:
        if f.serde_skip:
            continue
        ts_name = f.serde_rename or apply_rename(f.name, s.serde_rename_all)
        ts_type = rust_type_to_ts(f.rust_type, known_types)
        lines.append(f"  {ts_name}: {ts_type};")
    lines.append("}")
    return '\n'.join(lines)


def generate_ts_enum(e: RustEnum, known_types: set[str]) -> str:
    """Generate TypeScript type from a Rust enum."""
    all_unit = all(data is None for _, data in e.variants)

    if all_unit:
        values = []
        for name, _ in e.variants:
            ts_name = apply_rename(name, e.serde_rename_all)
            values.append(f'"{ts_name}"')
        return f"export type {e.name} = {' | '.join(values)};"

    if e.serde_tag:
        variants = []
        for name, data in e.variants:
            tag_val = apply_rename(name, e.serde_rename_all)
            if data and data != "struct":
                ts_data = rust_type_to_ts(data, known_types)
                if e.serde_content:
                    variants.append(f'{{ {e.serde_tag}: "{tag_val}"; {e.serde_content}: {ts_data} }}')
                else:
                    variants.append(f'{{ {e.serde_tag}: "{tag_val}" }} & {ts_data}')
            else:
                variants.append(f'{{ {e.serde_tag}: "{tag_val}" }}')
        return f"export type {e.name} =\n  | " + "\n  | ".join(variants) + ";"

    if e.serde_untagged:
        variants = []
        for name, data in e.variants:
            if data:
                variants.append(rust_type_to_ts(data, known_types))
            else:
                variants.append(f'"{name}"')
        return f"export type {e.name} = {' | '.join(variants)};"

    # Default: externally tagged
    variants = []
    for name, data in e.variants:
        if data:
            ts_data = rust_type_to_ts(data, known_types)
            variants.append(f'{{ {name}: {ts_data} }}')
        else:
            variants.append(f'"{name}"')
    return f"export type {e.name} = {' | '.join(variants)};"


def generate_ts_ops(functions: list[FfiFunction], known_types: set[str]) -> str:
    """Generate typed op wrapper functions."""
    # First pass: collect all type references used in ops
    referenced_types: set[str] = set()
    for fn in functions:
        if fn.return_category in ('ptr', 'void'):
            continue
        if fn.op_name.startswith('pulse.') or '.' not in fn.op_name:
            continue
        if fn.op_name in ADHOC_SHAPES:
            continue
        if fn.return_type and fn.return_type in known_types:
            referenced_types.add(fn.return_type)

    type_import = ', '.join(sorted(referenced_types))

    lines = [
        '// AUTO-GENERATED by scripts/generate.py — do not edit manually.',
        '// Source of truth: divinity_ffi.h (C header) + Rust FFI source (type enrichment)',
        '',
        'import type { PipelineResponse } from "../bridge.js";',
    ]
    if type_import:
        lines.append(f'import type {{ {type_import} }} from "./types.js";')
    lines.extend([
        '',
        '/**',
        ' * Execute a single pipeline operation.',
        ' * For multi-step pipelines, use window.omninet.run() directly.',
        ' */',
        'async function exec<T>(op: string, input: Record<string, unknown> = {}): Promise<T> {',
        '  const response: PipelineResponse = await window.omninet.run({',
        '    source: "sdk",',
        '    steps: [{ id: "r", op, input }],',
        '  });',
        '  if (!response.ok) throw new Error(response.error);',
        '  return response.result as T;',
        '}',
        '',
    ])

    JS_RESERVED = {'export', 'import', 'default', 'class', 'function', 'var', 'let', 'const',
                   'return', 'delete', 'new', 'this', 'typeof', 'void', 'switch', 'case',
                   'break', 'continue', 'for', 'while', 'do', 'if', 'else', 'try', 'catch',
                   'finally', 'throw', 'with', 'yield', 'async', 'await', 'enum', 'super',
                   'extends', 'implements', 'interface', 'package', 'private', 'protected',
                   'public', 'static'}

    # Group functions by module
    modules: dict[str, list[FfiFunction]] = {}
    for fn in functions:
        if fn.return_category == 'ptr':
            continue  # Skip constructors (return opaque handles)
        if fn.return_category == 'void':
            # Include void actions, skip destructors and callback plumbing
            if '_free' in fn.name:
                continue
            if any(pat in fn.name for pat in VOID_SKIP_PATTERNS):
                continue
        if fn.op_name.startswith('pulse.'):
            continue
        if '.' not in fn.op_name:
            continue
        mod = fn.op_name.split('.')[0]
        modules.setdefault(mod, []).append(fn)

    for mod_name in sorted(modules.keys()):
        mod_fns = modules[mod_name]
        safe_name = f"_{mod_name}" if mod_name in JS_RESERVED else mod_name
        lines.append(f"export const {safe_name} = {{")
        for fn in sorted(mod_fns, key=lambda f: f.op_name):
            method_name = fn.op_name.split('.', 1)[1]
            ts_method = snake_to_camel(method_name)

            # Determine return type
            if fn.op_name in ADHOC_SHAPES:
                ret_type = ADHOC_SHAPES[fn.op_name]
            elif fn.return_type and fn.return_type in known_types:
                ret_type = fn.return_type
            elif fn.return_category == "json":
                ret_type = "unknown"
            elif fn.return_category == "string":
                ret_type = "string"
            elif fn.return_category == "bool":
                ret_type = "boolean"
            elif fn.return_category in ("i32", "usize", "double"):
                ret_type = "number"
            elif fn.return_category == "bytes":
                ret_type = "Uint8Array"
            elif fn.return_category == "void":
                ret_type = "void"
            else:
                ret_type = "unknown"

            lines.append(f'  {ts_method}: (input: Record<string, unknown> = {{}}) =>')
            lines.append(f'    exec<{ret_type}>("{fn.op_name}", input),')

        lines.append("} as const;")
        lines.append("")

    return '\n'.join(lines)


def snake_to_camel(name: str) -> str:
    """Convert snake_case to camelCase."""
    parts = name.split('_')
    return parts[0] + ''.join(p.capitalize() for p in parts[1:])


# ── Main ──────────────────────────────────────────────────────

def main():
    omninet = OMNINET_PATH
    for i, arg in enumerate(sys.argv):
        if arg == '--omninet-path' and i + 1 < len(sys.argv):
            omninet = Path(sys.argv[i + 1])

    if not omninet.exists():
        print(f"Error: Omninet path not found: {omninet}", file=sys.stderr)
        sys.exit(1)

    print(f"Omninet path: {omninet}")
    print(f"Output dir: {OUTPUT_DIR}")

    # Pass 1: Parse Rust types
    print("\n── Pass 1: Parsing Rust types ──")
    structs, enums = parse_rust_types(omninet)
    print(f"  Found {len(structs)} structs, {len(enums)} enums")

    known_types = set(structs.keys()) | set(enums.keys()) | set(MANUAL_OVERRIDES.keys())

    # Pass 2A: Parse C header for complete op discovery
    print("\n── Pass 2A: Parsing C header (divinity_ffi.h) ──")
    c_functions = parse_c_header(omninet)
    print(f"  Found {len(c_functions)} divi_* functions")

    # Category breakdown from C header
    c_cats: dict[str, int] = {}
    for f in c_functions:
        c_cats[f.return_category] = c_cats.get(f.return_category, 0) + 1
    for cat, count in sorted(c_cats.items()):
        print(f"    {cat}: {count}")

    # Pass 2B: Build Rust type hints for enrichment
    print("\n── Pass 2B: Parsing Rust FFI sources (type enrichment) ──")
    rust_hints = build_rust_type_hints(omninet)
    typed_hints = sum(1 for _, (_, t) in rust_hints.items() if t is not None)
    string_hints = sum(1 for _, (c, _) in rust_hints.items() if c == "string")
    print(f"  Found {len(rust_hints)} Rust body hints ({typed_hints} with specific types, {string_hints} string returns)")

    # Merge
    print("\n── Merging C header + Rust enrichment ──")
    functions = merge_ops(c_functions, rust_hints)

    # Post-merge stats
    typed = sum(1 for f in functions if f.return_type)
    json_untyped = sum(1 for f in functions if f.return_category == 'json' and not f.return_type and f.op_name not in ADHOC_SHAPES)
    adhoc = sum(1 for f in functions if f.op_name in ADHOC_SHAPES)
    def is_sdk_op(f: FfiFunction) -> bool:
        if f.return_category == 'ptr':
            return False
        if f.return_category == 'void':
            if '_free' in f.name:
                return False
            if any(pat in f.name for pat in VOID_SKIP_PATTERNS):
                return False
        if f.op_name.startswith('pulse.'):
            return False
        if '.' not in f.op_name:
            return False
        return True

    pipeline_ops = sum(1 for f in functions if is_sdk_op(f))
    skipped_ptr = sum(1 for f in functions if f.return_category == 'ptr')
    skipped_free = sum(1 for f in functions if '_free' in f.name and f.return_category == 'void')
    skipped_callback = sum(1 for f in functions if f.return_category == 'void' and any(pat in f.name for pat in VOID_SKIP_PATTERNS))
    void_actions = sum(1 for f in functions if f.return_category == 'void' and is_sdk_op(f))
    skipped_pulse = sum(1 for f in functions if f.op_name.startswith('pulse.'))
    skipped_no_module = sum(1 for f in functions if '.' not in f.op_name and f.return_category not in ('ptr',))
    print(f"  Total functions: {len(functions)}")
    print(f"  SDK ops: {pipeline_ops} ({pipeline_ops - void_actions} with return values + {void_actions} void actions)")
    print(f"  Skipped: {skipped_ptr} ptr (constructors), {skipped_free} _free (destructors), {skipped_callback} callback plumbing, {skipped_pulse} pulse, {skipped_no_module} no-module")
    print(f"  Typed: {typed}, Ad-hoc mapped: {adhoc}, Untyped JSON: {json_untyped}")

    # Generate output
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # types.ts
    print("\n── Generating types.ts ──")
    type_lines = [
        "// AUTO-GENERATED by scripts/generate.py — do not edit manually.",
        "// Source: Omninet (auto-generated from Rust crate definitions)",
        f"// Structs: {len(structs)}, Enums: {len(enums)}",
        "",
    ]

    emitted_names: set[str] = set()
    for name, ts_def in MANUAL_OVERRIDES.items():
        if '\n' in ts_def:
            type_lines.append(f"export type {name} =")
            type_lines.append(ts_def + ";")
        else:
            type_lines.append(f"export type {name} = {ts_def};")
        type_lines.append("")
        emitted_names.add(name)

    for key in sorted(structs.keys()):
        actual_name = structs[key].name
        if actual_name in emitted_names:
            continue
        ts = generate_ts_struct(structs[key], known_types)
        if ts.count('{}') == 1 and ts.count('\n') <= 2:
            continue
        type_lines.append(ts)
        type_lines.append("")
        emitted_names.add(actual_name)

    for key in sorted(enums.keys()):
        actual_name = enums[key].name
        if actual_name in emitted_names:
            continue
        ts = generate_ts_enum(enums[key], known_types)
        if ts.endswith('= ;'):
            continue
        type_lines.append(ts)
        type_lines.append("")
        emitted_names.add(actual_name)

    types_content = '\n'.join(type_lines)
    (OUTPUT_DIR / "types.ts").write_text(types_content)
    print(f"  Wrote {len(type_lines)} lines to types.ts")

    # ops.ts
    print("\n── Generating ops.ts ──")
    ops_content = generate_ts_ops(functions, known_types)
    (OUTPUT_DIR / "ops.ts").write_text(ops_content)
    ops_lines = ops_content.count('\n')
    print(f"  Wrote {ops_lines} lines to ops.ts")

    # manifest.json
    manifest = {
        "generated_at": __import__('datetime').datetime.now().isoformat(),
        "omninet_path": str(Path(os.environ.get("OMNINET_PATH", "../Omninet"))),
        "strategy": "C header (discovery) + Rust source (type enrichment)",
        "stats": {
            "structs": len(structs),
            "enums": len(enums),
            "c_header_functions": len(c_functions),
            "rust_type_hints": len(rust_hints),
            "sdk_ops": pipeline_ops,
            "void_actions": void_actions,
            "typed_returns": typed,
            "adhoc_mapped": adhoc,
            "untyped_json": json_untyped,
        },
        "untyped_json_ops": sorted([
            f.op_name for f in functions
            if f.return_category == 'json' and not f.return_type and f.op_name not in ADHOC_SHAPES
            and f.return_category not in ('ptr', 'void')
            and '.' in f.op_name and not f.op_name.startswith('pulse.')
        ]),
    }
    (OUTPUT_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2))

    print(f"\n── Done. Generated SDK in {OUTPUT_DIR} ──")


if __name__ == "__main__":
    main()
