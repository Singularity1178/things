# Upstream source map

Where each structure is defined in `https://github.com/koharu-rs/koharu`, for
when you need to extend the decoder or check a layout.

The wire format is *not* defined by Koharu — it comes from the
[`revision`](https://github.com/surrealdb/revision) crate (v0.30), a SurrealDB
project. Koharu adds `#[revisioned(...)]` attributes to its own types.

## Getting the source

```bash
git clone --depth 1 --filter=blob:none --no-checkout https://github.com/koharu-rs/koharu.git
cd koharu-src
git sparse-checkout init --cone
git sparse-checkout set crates
git checkout
```

## Storage layer — `crates/koharu-storage/`

| What | Where |
|---|---|
| container layout, header, checksum, slot selection | `src/format.rs` |
| `DocumentId`, `Revision`, `BlobId`, `PatchId` | `src/ids.rs` |
| format documentation | `README.md` |

`src/format.rs` is the authoritative answer for "is it encrypted?" — no; it is
magic + version + length + BLAKE3.

## Scene layer — `crates/koharu-scene/src/`

| What | Where |
|---|---|
| `StoredState`, `StoredPage`, `StoredEntity`, `StoredComponentEntry` | `state.rs` (~lines 969–1007) |
| `StoredComponent`, `ComponentKey` | `component.rs` (~line 63–86) |
| `EntityId`, `RelationId`, `ProducerId` | `id.rs` |
| entity tree / pages / slotmap internals | `state.rs` |

## Component payloads — `crates/koharu-scene/src/components/`

| File | Types | Component `kind` |
|---|---|---|
| `text.rs` | `SourceText`, `Translation`, `TextContent`, `TextRole`, `LanguageTag` | `text.source`, `text.translation`, `text.content`, `text.role` |
| `assets.rs` | `Assets`, `Asset`, `AssetMetadata`, `AssetRole` | `assets` |
| `analysis.rs` | `Region`, `RegionKind`, `OcrAnalysis`, `TextDirection`, `DetectionAnalysis`, `DetectionLabel` | `analysis.region`, `analysis.ocr`, `analysis.detection` |
| `spatial.rs` | `Point`, `Geometry`, `Visibility` | `geometry`, `visibility` |
| `structure.rs` | `Page`, `Project`, `EntityOrigin`, `Relation`, `RelationKind` | `page`, `project`, `entity.origin`, `relation` |
| `layers.rs` | `TextLayout` (**revision 2**), `TextLayoutKind`, `Typography`, `RasterLayer`, `RasterLayerKind`, `TextAlignment`, `WritingMode`, `FontStyle` | `layer.text`, `text.typography`, `layer.raster` |
| `groups.rs` | `Group`, `TextGroup` | `group`, `text.group` |
| `provenance.rs` | `Authored<T>`, `Origin`, `Generation` | (attached to most kinds) |

## Untested payload layouts

Two kinds appear in the source but had no instances in the reference project,
so their decoders are transcribed rather than verified. They are deliberately
**absent** from `DECODERS` in `khrlib.py`, which means such components decode
to `value=None` instead of raising:

```rust
// components/structure.rs
#[revisioned(revision = 1)]
pub struct Project {
    pub source_locale: Option<LanguageTag>,
    pub target_locales: Vec<LanguageTag>,
}

// components/structure.rs
#[revisioned(revision = 1)]
pub struct Relation {
    pub origin: Origin,
    pub kind: RelationKind,
    pub source: EntityId,
    pub target: EntityId,
}
```

To add them:

```python
def _project(r):
    r.uint()                                       # struct revision
    src = r.opt(lambda rr: lang(rr))
    tgt = [lang(rr) for _ in range(r.uint())]
    return dict(source_locale=src, target_locales=tgt)

def _relation(r):
    r.uint()                                       # struct revision
    o = origin(r)
    r.uint(); k = r.string()                       # RelationKind newtype
    src = r.ent(); tgt = r.ent()
    return dict(origin=o, kind=k, source=src[1], target=tgt[1])
```

Then register in `DECODERS`. Re-run and confirm `consumed == total` plus zero
component errors.

## The `revision` crate — `github.com/surrealdb/revision`

| What | Where |
|---|---|
| varint integer encoding | `src/implementations/primitives.rs` (`encode_u64` / `decode_u64`) |
| `Vec<T>` length-prefix rules | `src/implementations/vecs.rs` |
| `Option<T>` | `src/implementations/option.rs` |
| `Uuid` (raw 16 bytes) | `src/implementations/uuid.rs` |
| where the leading revision byte is emitted | `revision-derive/src/expand/mod.rs`, `ser.rs` |
| enum discriminant assignment | `revision-derive/src/expand/common.rs` |

The decisive line for Rule 1 is in `revision-derive/src/expand/mod.rs`:

```rust
impl ::revision::SerializeRevisioned for #name {
    fn serialize_revisioned<W: std::io::Write>(&self, writer: &mut W) -> Result<(), Error> {
        ::revision::SerializeRevisioned::serialize_revisioned(
            &<Self as ::revision::Revisioned>::revision(), writer)?;   // <-- the byte
        #serialize
    }
}
```

Types with a **manual** `SerializeRevisioned` impl (like `SourceText` and
`Translation` in `components/text.rs`) bypass this, which is exactly why they
have no header.

## Format-version check

`crates/koharu-storage/src/format.rs` pins the container:

```rust
const MAGIC: &[u8; 8] = b"KHRSTATE";
const VERSION: u32 = 1;
const HEADER_BYTES: usize = 8 + 4 + 8 + 32;
```

If a future version bumps this, decoding needs revisiting. `khrlib.read_state()`
raises on an unexpected version rather than guessing.

## Machine model names seen in `Origin`

Read out of real `Generation` values, useful for identifying pipeline config:

| Producer | Model |
|---|---|
| `dev.koharu.pipeline.detection` | `mayocream/koharu-layout-rfdetr-seg-2xl-1152` |
| `dev.koharu.pipeline.ocr` | `paddleocr-vl-1.6` |
| `dev.koharu.pipeline.inpainting` | `lama` |
| `dev.koharu.pipeline.translation` | `gemini` (target locale `en-US`) |

## Licence note

Koharu is dual-licensed MIT / Apache-2.0. The `revision` crate is Apache-2.0.
The code in this folder is an independent reimplementation for reading the
on-disk format; it links against neither project.