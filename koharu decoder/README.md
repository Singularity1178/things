# Koharu `.khrproj` format — decoding reference and toolkit

Reverse-engineered from `https://github.com/koharu-rs/koharu` and verified
byte-for-byte against a real 48-page project. Everything here is tested, not
inferred — see [Verification](#verification).

**If you are here to decode a project, you do not need to read the format
sections.** Copy the folder, `pip install blake3`, and run the two commands in
[Quick start](#quick-start).

---

## TL;DR for a future session

The `.khrproj` "file" is a **directory**. There is no encryption anywhere.

```
<name>.khrproj/
├── project.lock          empty, single-writer lock
├── state-a.khr           alternating full snapshots (pick highest revision)
├── state-b.khr
└── blobs/
    └── <first 2 hex>/     content-addressed BLAKE3 filenames
        <64 hex>          stored as RAW PLAINTEXT images/audio
```

- `state-*.khr` = 52-byte header + one `revision`-crate bincode blob.
- Header carries a BLAKE3 digest of the body — it is an **integrity** check,
  not a secret. Editing requires recomputing it.
- The scene payload decodes to pages → entities → components → relations.
- Text is stored per region as `SourceText` (Japanese) + `Translation` (English).

**The one thing that will waste your time if you don't know it:** every type
declared `#[revisioned]` writes **its own revision header byte before its
fields**, including transparent newtype wrappers. See
[Rule 1](#rule-1--every-revisioned-type-writes-a-leading-byte).

---

## Quick start

```bash
pip install blake3

# dump everything: text, images, per-page files, JSON
python khrdump.py "<path to>.<name>.khrproj" -o out

# preview a colour change (dry run, writes nothing)
python khrpatch.py "<path to>.<name>.khrproj" --rgb ff0000

# actually apply it
python khrpatch.py "<path to>.<name>.khrproj" --rgb ff0000 --apply --all-slots
```

`khrdump.py` prints a summary and writes:

| Path | Contents |
|---|---|
| `out/project.json` | every page, text region, relation, blob reference |
| `out/images/` | page scans + text masks, renamed by page label |
| `out/images/manifest.json` | which blob went where |
| `out/text/NNNN.txt` | one file per page, source + translation |
| `out/translation.txt` | whole script, side by side |

Useful library calls:

```python
import khrlib as K

d   = K.load(project)              # picks newest valid slot automatically
sc  = d['scene']                   # pages / entities / components / relations
rows = K.text_regions(sc)          # flat, resolved text rows
K.page_assets(sc)                  # per-page image blob ids
K.blob_path(project, blob_id)      # absolute path to a blob file
```

---

## Repository map

| File | Purpose |
|---|---|
| `khrlib.py` | the whole format: reader, writer, colour patcher. No dependencies beyond `blake3`. |
| `khrdump.py` | CLI — export project to text/images/JSON |
| `khrpatch.py` | CLI — change `Typography.color` safely |
| `FORMAT.md` | the format itself, in detail (byte layouts, struct definitions) |
| `REFERENCE.md` | where each type lives in the upstream Rust source |

---

# FORMAT.md — how the bytes work

## Container: `state-a.khr` / `state-b.khr`

Fixed 52-byte header, then body.

| Offset | Size | Field |
|---|---|---|
| 0 | 8 | magic `KHRSTATE` |
| 8 | 4 | format version, u32 LE (`1`) |
| 12 | 8 | body length, u64 LE |
| 20 | 32 | `blake3(body)` |
| 52 | … | body |

The two slots alternate. On open, Koharu validates both and takes the newest
whose blobs all exist, falling back to the older slot if the newer one is
corrupt. `khrlib.newest_state()` implements that selection.

The body is `revision::to_vec(StoredState)` from the
[`revision`](https://github.com/surrealdb/revision) crate (v0.30), which is a
bincode-style format with **varint** integers rather than fixed-width ones.

## Integer encoding

Integers are LEB-less "tagged" varints (`revision` calls it its optimised
encoding). See `revision-src/src/implementations/primitives.rs`.

| Byte | Meaning |
|---|---|
| `0..250` | that value, directly |
| `251` | u16 LE follows (2 bytes) |
| `252` | u32 LE follows (4 bytes) |
| `253` | u64 LE follows (8 bytes) |
| `254` / `255` | error / invalid |

`f32` / `f64` / `[u8; N]` / `Uuid` are **always raw fixed-width**, never
varint. `bool` is one byte. `String` / `Vec<u8>` / byte blobs are
varint-length + raw bytes. `Option<T>` is one tag byte (`0` none, `1` some)
then the value if present.

## Rule 1 — every `revisioned` type writes a leading byte

**This is the single most important thing to know.** Every type declared
`#[revisioned(revision = N)]` emits a varint-encoded `u16` holding its schema
revision *before* its fields. This includes:

- structs (`StoredPage`, `StoredEntity`, `Typography`, …)
- enums (`Origin`, `TextDirection`, `RegionKind` — header **and** discriminant)
- transparent newtype wrappers (`DocumentId(Uuid)`, `EntityId(Uuid)`,
  `BlobId([u8;32])`, `Revision(u64)`, `ProducerId(String)`,
  `LanguageTag(String)`, `RelationKind(String)`, `AssetRole(String)`)

Two consequences that bite:

```text
// a Vec<BlobId>: the length prefix is per VECTOR, but the revision byte is
// per ELEMENT — inside the loop, not before it
count = varint
repeat count times:
    revision byte        <-- per element
    32 raw bytes

// a Vec<EntityId> behaves the same way
```

And enums cost two bytes, not one:

```text
revision byte, then u32 discriminant (as a varint), then fields
```

If your parse "mostly works but drifts", this is why. Symptom to recognise: you
successfully read several fields, then hit a nonsensical `varint tag 255`.

### Rule 2 — the three exceptions

| Type | Why |
|---|---|
| `Uuid` | hand-written impl, raw 16 bytes, **no** header |
| `SourceText`, `Translation` | *manual* `SerializeRevisioned` impls, **no** header |
| `Point` (`geometry`) | `#[revisioned]` — **does** get a header, unlike `Uuid` |

`SourceText` / `Translation` are `Authored<String> { value, origin }` followed
by `Option<LanguageTag>`.

## Storage layer: `StoredState`

```text
revision byte                       // StoredState schema revision
DocumentId      -> revision byte + 16 raw uuid bytes
Revision        -> revision byte + varint
blobs: Vec<BlobId>
    varint count
    per element: revision byte + 32 raw bytes
payload: Vec<u8> -> varint length + raw bytes
```

The payload is the **last** field. `khrlib.payload_offset()` re-walks the
prefix to find it exactly rather than assuming an offset — this matters,
because an off-by-N here silently corrupts everything downstream.

## Scene layer: `StoredState`

```text
revision byte                              // scene schema revision
page_order_epoch: varint
project_components: Vec<StoredComponentEntry>
pages: Vec<StoredPage>
    revision byte
    EntityId       -> revision byte + 16 bytes
    epoch: varint
    entities: Vec<StoredEntity>
        revision byte
        EntityId   -> revision byte + 16 bytes
        parent: Option<EntityId>
        components: Vec<StoredComponentEntry>
relations: Vec<StoredRelation>
    revision byte
    RelationId    -> revision byte + 16 bytes
    Relation      -> revision byte
                     origin: Origin
                     kind:   RelationKind  -> revision byte + String
                     source: EntityId      -> revision byte + 16 bytes
                     target: EntityId      -> revision byte + 16 bytes
    components: Vec<StoredComponentEntry>
```

## `StoredComponentEntry`

Every component, everywhere, uses this envelope:

```text
revision byte                            // StoredComponentEntry
ComponentKey  -> revision byte + String  // e.g. "dev.koharu.text.source"
StoredComponent:
    revision byte
    schema: varint
    payload: varint length + raw bytes   // the actual type, encoded
    record_refs: Vec<EntityId>           -> count, then per element revision+16
    blob_refs:   Vec<BlobId>             -> count, then per element revision+32
```

`payload` is the concrete component struct, encoded independently. Its layout
depends on `kind`. Known kinds and their payloads:

| `kind` | payload struct |
|---|---|
| `dev.koharu.text.source` | `Authored<String>` + `Option<LanguageTag>` — **no header** |
| `dev.koharu.text.translation` | same — **no header** |
| `dev.koharu.text.content` | `TextContent { origin }` |
| `dev.koharu.text.role` | `TextRole { origin, role: String }` |
| `dev.koharu.text.group` | `TextGroup {}` (empty) |
| `dev.koharu.text.typography` | `Typography { … }` — see below |
| `dev.koharu.layer.text` | `TextLayout { origin, kind, angle_degrees }` — **revision 2** |
| `dev.koharu.layer.raster` | `RasterLayer { origin, name, kind }` |
| `dev.koharu.geometry` | `Geometry { origin, points: Vec<Point> }` |
| `dev.koharu.analysis.region` | `Region { origin, kind, label }` |
| `dev.koharu.analysis.ocr` | `OcrAnalysis { origin, direction, confidence, line_boundaries }` |
| `dev.koharu.analysis.detection` | `DetectionAnalysis { origin, labels }` |
| `dev.koharu.assets` | `Assets { values: BTreeMap<AssetRole, Asset> }` |
| `dev.koharu.page` | `Page { label, width: f64, height: f64 }` |
| `dev.koharu.group` | `Group { origin, name }` |
| `dev.koharu.visibility` | `Visibility { origin, visible, opacity: f32 }` |
| `dev.koharu.entity.origin` | `EntityOrigin { origin }` |
| `dev.koharu.project` | `Project { source_locale, target_locales }` |
| `dev.koharu.relation` | `Relation { origin, kind, source, target }` |

`Typography` in field order, because colour patching depends on it:

```text
revision byte
origin: Origin
preferred_font: Option<String>
font_weight:    Option<u16>
font_style:     Option<FontStyle enum>
size:           Option<f32>
auto_fit:       bool                      (1 byte)
color:          Option<[u8;4]>            <-- 1 tag + 4 RAW bytes, RGBA
stroke_color:   Option<[u8;4]>
stroke_width:   Option<f32>
alignment:      Option<TextAlignment enum>
writing_mode:   Option<WritingMode enum>
extensions:     BTreeMap<String, String>
```

## `Origin` — provenance

Present on nearly every component, tells you what produced it:

```text
revision byte
discriminant 0 -> User
discriminant 1 -> Generated(Generation):
                    revision byte
                    ProducerId -> revision byte + String
                    model:      Option<String>
                    confidence: Option<f32>
```

In practice this is where you learn which ML model touched the data, e.g.
`dev.koharu.pipeline.detection` / `mayocream/koharu-layout-rfdetr-seg-2xl-1152`,
`dev.koharu.pipeline.ocr` / `paddleocr-vl-1.6`,
`dev.koharu.pipeline.inpainting` / `lama`.

## The scene graph is three layers, joined by relations

This is not obvious from the data and cost real time to work out. A text
region is **not** one entity. It is three:

```text
   layout entity                    text entity                   region entity
   ─────────────                    ──────────                   ─────────────
   text.typography                  text.source        ◄──presents text.content
   layer.text                       text.translation                      text.role
   (presentation)                   (the words)                             ◄──recognized-from
                                                                            analysis.ocr
                                                                            analysis.region
                                                                            analysis.detection
                                                                            geometry      (bbox)
```

Resolve them with the relations:

| relation | direction | meaning |
|---|---|---|
| `recognized-from` | text → region | where the OCR/detection came from |
| `presents` | layout → text | which layout renders this text |
| `inside` | text → bubble | containment |
| `fits-to` | text → panel | fits inside a panel |
| `flows-in` | bubble → bubble | reading-order chain |

`khrlib.text_regions()` does this join for you and returns flat rows carrying
`bbox`, `src`, `tr`, `color`, etc.

**Reading order is not stored as an index.** `flows-in` encodes a chain between
bubbles, but for per-page ordering the pragmatic approach is to sort by
**descending x, then ascending y** — manga reads right-to-left. This is a
reconstruction from geometry, not an authoritative field.

## Blobs

`blobs/<first-2-hex>/<64-hex>` — filename is `blake3(bytes)`, contents are the
raw bytes with no envelope. Common types seen: JPEG page scans, PNG text masks,
RIFF WAV. Resolve a component's `blob_refs`, or for page scans read the
`dev.koharu.assets` component on the page's root entity (roles `source` and
`text-mask`).

---

## Writing

### What is safe

Surgical **in-place byte patching of fixed-width fields**. `color` is
`Option<[u8;4]>` = 1 tag byte + 4 raw bytes. Overwriting the 4 bytes cannot
change any length, so the body length is unchanged and only the header digest
needs recomputing. `khrlib.set_typography_color()` does this.

```python
raw = bytearray(state_bytes)
for off, old in colour_offsets:
    assert raw[off:off+4] == old        # prove we are where we think we are
    raw[off:off+4] = b'\xff\x00\x00\xff'
raw[20:52] = blake3(bytes(raw[52:])).digest()
```

Three defences that each caught a real bug during development — keep them:

1. `assert raw[off:off+4] == old` before writing. Catches a wrong offset.
2. `assert len(new_body) == len(old_body)` after patching. Catches any edit
   that changed framing.
3. Verify the checksum on read, before doing anything. A file can have a valid
   magic, valid length, valid digest, and a corrupt payload.

### What is not safe

Adding or removing a field changes lengths, which shifts every subsequent
offset and the varint prefixes. That requires a real re-encoder. In particular
you **cannot** set a colour on a region where `color` is `None` (8 of 353 in the
reference project) by patching — the `None` tag byte would have to become a
`Some` tag and four bytes would have to be inserted.

### Pitfalls that actually happened

- **Double-counted the header offset** (added the 52-byte header to an offset
  that already included it). Result: magic valid, length valid, BLAKE3 valid,
  payload corrupt. Only a diff against a backup caught it.
- **Passed the whole state body to the scene walker** instead of just the
  payload, which is nested inside it.

The lesson that generalises: **never trust a checksum as proof your edit was
semantically right.** It only proves the bytes you produced hash to the digest
you just recomputed. Diff against the original and re-decode.

### Always patch both slots

Koharu picks whichever slot is newer. Patching one leaves the other as a
latent fallback — and if Koharu re-saves, the colour silently reverts. Use
`--all-slots`.

### Backups

`--apply` writes `<slot>.bak` before replacing. Koharu's own writer uses a
temp file plus atomic rename, which `khrlib.write_state()` mirrors.

---

## Verification

How to know a change is safe, in order of increasing confidence:

1. `K.read_state()` verifies magic, body length, and BLAKE3.
2. Re-decode and assert `consumed == total` — byte-exact, no trailing bytes.
3. Count component decode errors; must be zero across all kinds.
4. **Diff against the pre-change backup.** Group differing offsets into runs and
   assert each run is exactly the field you meant to change, and that runs are
   where your model said they would be.
5. **Semantic comparison**: full-decode both versions and assert that every
   component payload is byte-identical *except* the ones you intended to
   change, and that all relations are identical.

Step 5 is the one that actually proves intent. Steps 1–3 prove well-formedness,
not correctness.

Reference project results after changing all colours to red:

| Check | Result |
|---|---|
| BLAKE3 verifies | pass (both slots) |
| Scene parse byte-exact | 1833219/1833219 and 1832937/1832937 |
| Component decode errors | 0 |
| Component payloads changed | 345, all `text.typography` |
| Other kinds changed | 0 (9144 / 9143 records identical) |
| Relations identical | yes, 1333 |
| Header bytes changed | exactly 32 (the digest) |

Not verified: opening in the Koharu GUI and inspecting the canvas. If text
still renders black, suspect the 8 regions with no colour set, or a
render-side default — not the decode.

---

## Known gaps and caveats

- **`OcrAnalysis.line_boundaries` is decoded but never exercised.** The
  reference project has 353 OCR components, all with an empty `line_boundaries`
  vector. The `Vec<[Point; 4]>` path is written per the struct definition but
  has not been run against real data. If you hit a parse failure in a project
  with populated OCR line data, that is the first suspect.
- **`dev.koharu.project` and `dev.koharu.relation` payloads have no decoders.**
  They did not appear in the reference project's components (project
  components were empty; relations carry no components there either), so the
  layouts are transcribed from the Rust source and untested. Structs are in
  `REFERENCE.md`.
- **Page ordering is reconstructed**, not read from the format.
- **`TextDirection` / `TextAlignment` / `WritingMode` / `FontStyle` /
  `RasterLayerKind` / `TextLayoutKind` discriminants are decoded as integers,
  not mapped to names.** Need the enum definitions from
  `koharu-scene/src/components/layers.rs` and `analysis.rs` to name them.
- **The project may be re-saved while you work on it.** Revisions were observed
  climbing during a session, so a project open in the GUI can change under you.
  Re-read before writing, and always prefer `newest_state()` over a hardcoded
  slot.
- **Only format version 1 is handled.** The reader rejects others.
- Garbage collection removes blobs unreferenced by *both* slots, so a blob
  present on disk may legitimately be absent from the newest state. Not
  necessarily corruption.

---

## PowerShell / console note

Japanese text renders as mojibake through `Get-Content` and the default
console codepage, even though the files are perfectly valid UTF-8. It is a
display problem, not a data problem. To check:

```bash
$env:PYTHONIOENCODING='utf-8'
python -c "print(open(r'out\text\0003.txt', encoding='utf-8').read())"
```