> **[Omnidea](https://github.com/neonpixy/omnidea)** / **[Library](https://github.com/neonpixy/library)** / **SDK** · For AI-assisted development, see [Library CLAUDE.md](https://github.com/neonpixy/library/blob/main/CLAUDE.md).

# @omnidea/net


TypeScript SDK for the Omninet protocol. 860 typed operations, auto-generated from the Rust source, wrapping the pipeline API that every Omninet program speaks through.

```typescript
import { crown, vault, hall } from "@omnidea/net";

const key = await crown.keyringGeneratePrimary();
console.log("Your Crown ID:", key.crown_id);
```

---

## How It Works

Every Omninet program runs in a WebView. The native shell (Omny) injects `window.omninet` — a bridge that connects your TypeScript to the Zig orchestrator, which calls into 29 Rust protocol crates via 1,040 C FFI functions.

```
Your Program (TypeScript)
    |
    |  import { crown } from "@omnidea/net"
    |  await crown.keyringGeneratePrimary()
    v
@omnidea/net .............. this package (860 typed ops)
    |
    |  window.omninet.run({ steps: [{ op: "crown.keyring_generate_primary" }] })
    v
window.omninet ............ bridge injected by native shell
    |
    |  JSON over WebIDL (Throne) or fetch (fallback)
    v
Zig Orchestrator .......... comptime auto-dispatch, pipeline execution
    |
    v
Rust FFI .................. 1,040 extern "C" functions
    |
    v
29 Rust Crates ............ the protocol (6,700+ tests)
```

You write TypeScript. The SDK handles the rest.

---

## Installation

```bash
npm install @omnidea/net
```

> Note: The SDK requires `window.omninet` to be present, which is injected by the Omny browser shell. It won't work in a regular browser unless you're connecting through a Tower gateway with the fetch bridge.

---

## Single Operations

Every operation is available as a typed function on its namespace:

```typescript
import { crown, vault, sentinal } from "@omnidea/net";

// Create identity
const key = await crown.keyringGeneratePrimary();

// Get profile
const profile = await crown.soulProfile();

// Check vault status
const unlocked = await vault.isUnlocked();
```

Each function wraps a single pipeline step. Under the hood, `crown.soulProfile()` becomes:

```json
{
  "source": "sdk",
  "steps": [{ "id": "r", "op": "crown.soul_profile", "input": {} }]
}
```

---

## Multi-Step Pipelines

For workflows that chain multiple operations, use `window.omninet.run()` directly:

```typescript
const result = await window.omninet.run({
  source: "my-program",
  steps: [
    { id: "key", op: "vault.content_key", input: { idea_id: "abc-123" } },
    { id: "doc", op: "hall.read", input: { path: "/notes.idea", content_key: "$key.result" } }
  ]
});

if (result.ok) {
  console.log(result.result);
} else {
  console.error(result.error, "at step", result.failed_step);
}
```

Steps execute sequentially. Any string value starting with `$` references a previous step's output — `$key.result` resolves to whatever the `key` step returned. Nested access works: `$step.field.subfield`.

---

## Platform Operations

Some operations live outside the pipeline — things the native shell handles directly (identity lifecycle, daemon config, Tower status):

```typescript
// Platform ops use a separate channel
const state = await window.omninet.platform("crown.state", {});

// Subscribe to push events
window.omninet.on("omnibus.event", (data) => {
  console.log("Received:", data);
});
```

---

## The Bridge

The SDK exports the bridge types for when you need to work at a lower level:

```typescript
import type {
  PipelineStep,
  PipelineRequest,
  PipelineResponse,
  OmninetBridge,
} from "@omnidea/net";
```

| Type | What It Is |
|------|-----------|
| `PipelineStep` | `{ id, op, input }` — one step in a pipeline |
| `PipelineRequest` | `{ source, steps }` — a complete pipeline to execute |
| `PipelineResponse` | `{ ok, result }` on success or `{ ok, error, failed_step, step_index }` on failure |
| `OmninetBridge` | The `window.omninet` interface — `run()`, `platform()`, `capabilities()`, `on()`, `off()` |

---

## Namespaces

860 operations organized into 35 namespaces:

| Namespace | Ops | What It Covers |
|-----------|----:|---------------|
| `advisor` | 46 | AI cognitive loop, thoughts, synapses, providers, skills |
| `appcatalog` | 15 | App lifecycle, manifest management |
| `bridge` | 2 | Bridge utilities |
| `bulwark` | 27 | Trust layers, permissions, Kids Sphere, child safety |
| `commerce` | 29 | Products, cart, orders, checkout, reviews |
| `contacts` | 7 | Module registry, catalog queries |
| `crown` | 34 | Identity, keyring, soul, profile, personas, blinding |
| `device` | 23 | Device pairing, fleet management, sync |
| `discovery` | 2 | mDNS local network discovery |
| `email` | 3 | Pub/sub event registration |
| `exporter` | 3 | Export format registration |
| `formula` | 11 | Spreadsheet formula engine |
| `fortune` | 19 | Ledger, treasury, UBI, demurrage, cash |
| `globe` | 22 | ORP events, filters, relay pool, privacy |
| `hall` | 11 | Encrypted .idea file I/O |
| `ideas` | 60 | Digits, schemas, domain helpers, packages |
| `importer` | 2 | Import format registration |
| `jail` | 11 | Trust graphs, flags, graduated response |
| `kingdom` | 57 | Communities, charters, proposals, voting, federation |
| `lingo` | 3 | Babel obfuscation, translation |
| `magic` | 77 | Document state, canvas, renderers, tools, history |
| `omnibus` | 33 | Node runtime, relay server, services |
| `oracle` | 33 | Onboarding, sovereignty tiers, workflows, hints |
| `pager` | 9 | Notification queue |
| `phone` | 4 | RPC handler registration |
| `physical` | 42 | Places, regions, meetups, presence, deliveries |
| `polity` | 70 | Rights, duties, protections, consent, constitutional review |
| `quest` | 20 | Missions, achievements, XP, challenges |
| `regalia` | 17 | Design tokens, layout, theming, materials |
| `sentinal` | 22 | Encryption, key derivation, key slots, onion |
| `undercroft` | 16 | System health, observatory |
| `vault` | 32 | Encrypted storage, manifest, collectives |
| `yoke` | 62 | Versions, timelines, ceremonies, provenance |
| `zeitgeist` | 23 | Tower directory, query routing, trends, cache |

---

## Generated Types

The SDK includes 823 TypeScript interfaces and 393 type aliases, auto-generated from the Rust crate types that have `Serialize`/`Deserialize` derives. These are the types you'll see in return values:

```typescript
import type { OmniEvent, Signature, Community, Proposal } from "@omnidea/net";
```

All generated code lives in `src/generated/` — **do not edit manually**. Run `npm run generate` to regenerate from the current Omninet source.

---

## Code Generation

The SDK is auto-generated from the Rust source by a 3-pass Python script:

```bash
npm run generate
```

**Pass 1:** Parse all Rust types with `Serialize`/`Deserialize` derives from 29 crates. Handles `#[serde(...)]` attributes (rename_all, tag, content, untagged, flatten). Produces 823 structs + 393 enums as TypeScript.

**Pass 2A:** Parse the C header (`divinity_ffi.h`) to discover all 1,040 `divi_*` functions. Classifies return types: json, string, i32, bool, usize, double, bytes, ptr, void.

**Pass 2B:** Read Rust FFI source files for type enrichment — traces return types from function bodies to determine which Rust struct each function actually returns. Provides 535 type hints.

**Merge:** C header gives completeness (every function). Rust enrichment gives type precision (what it returns). The result: 860 SDK operations, 160 with fully typed returns, 323 returning `unknown` (enrichment gaps — will improve over time).

### Files

| File | What It Contains | Edit? |
|------|-----------------|-------|
| `src/bridge.ts` | Bridge types (PipelineStep, etc.) | Yes — hand-maintained |
| `src/index.ts` | Re-exports | Yes — hand-maintained |
| `src/generated/ops.ts` | 860 operations | No — auto-generated |
| `src/generated/types.ts` | 823 interfaces + 393 types | No — auto-generated |
| `src/generated/manifest.json` | Codegen metadata + stats | No — auto-generated |
| `scripts/generate.py` | The code generator (1,137 lines) | Yes — the codegen source |

---

## Known Limitations

- **323 operations return `unknown`** — the type enrichment pass couldn't determine the concrete return type. These still work; you just don't get type inference on the result. This will improve as the Rust→TypeScript type mapping gets richer.
- **All inputs are `Record<string, unknown>`** — the codegen doesn't yet generate typed input parameters. You need to know which fields each operation expects (check the Omninet crate CLAUDE.md files for per-operation docs).
- **No offline mode** — the SDK requires `window.omninet` to be present. There's no mock or offline fallback.
- **No streaming** — pipeline results are returned as a single response. Real-time data flows through `window.omninet.on()` events.

---

## Two Bridge Implementations

The `window.omninet` bridge is provided by the Omny browser. There are two implementations:

**Tauri Bridge (primary)** — `window.omninet` routes calls through Tauri's `invoke()` to the Rust backend, which forwards to the daemon via IPC. Native, no HTTP roundtrip.

**Fetch Bridge (fallback)** — Encodes operations as base64 URL paths and fetches `omny://api/run/*`. Works anywhere the Omny daemon is running.

Both bridges accept the same JSON contract. The SDK works identically on either. Programs don't need to know which bridge they're talking through.

---

## License

Licensed under the Omninet Covenant License. See [Covenant](https://github.com/neonpixy/covenant) for details.
