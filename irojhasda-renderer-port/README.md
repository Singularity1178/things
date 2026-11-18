# irojhasda renderer port — handoff package

This folder exists so a second agent can port the good parts of the
**`newest irojhasda-main - Copy (2)`** checkout into
**`irojhasda-main`**, without having to redo the comparison.

Start with **`PROMPT.md`** — that is the task prompt. Read
**`MERGE_NOTES.md`** before touching code; it contains the file-by-file plan,
the exact caveats, and the list of things that must *not* be ported.

## Contents

| Path | What it is |
| --- | --- |
| `PROMPT.md` | The delegated task, constraints, acceptance criteria. |
| `MERGE_NOTES.md` | Detailed merge guidance, per-file plan, traps, verification. |
| `base/bapet/*.py` | Snapshot of the target (`irojhasda-main`) files that matter. |
| `copy2/bapet/*.py` | Snapshot of the source (`Copy (2)`) versions of the same files. |
| `patches/*.patch` | Unified diffs `base -> copy2` for renderer/model/gltf/pet_render, with `a/bapet/...` headers so `git apply -p1` works from a repo root. |
| `tests/test_model_loading.py` | Target repo's model-loading unit tests (10 checks). |
| `tests/test_asset_runtime.py` | Target repo's 200-entry live integration test (`--live`). |
| `tests/test_lazy_models.copy2.py` | Copy (2)'s bake-equality / lazy-loading test, useful as a reference to adapt. |
| `tools/benchmark_startup.py` | Fresh-process startup benchmark used for the numbers below. |
| `docs/PERFORMANCE-main.md` | The target repo's performance notes. |
| `docs/PERFORMANCE-copy2.md` | Copy (2)'s performance notes (source of the per-frame claims). |

Snapshots are read-only reference material. Do not edit them; edit the real
checkout `C:\Users\ADMIN\Downloads\irojhasda-main` (or wherever it lives for you).

## The one-paragraph verdict

`irojhasda-main` is the better overall design and should stay the base: zero
GLB reads, zero GL context, and zero pet windows at startup, bounded residency,
single-flight decode off the GUI thread. Copy (2) wins the **per-frame rendering
layer** (shared shader programs, uniform-value caching, single readback,
`flip_y`, reusable frame buffer, GPU leak fixes) and has a small bake-time win
in the animation sampler. Port those into the base; do not adopt Copy (2)'s
lazy `Character`/`Pet` shells, portrait cache, `model_cache` policy, or
load-everything start-up.

## Measured on the same machine

| Scenario | `irojhasda-main` | Copy (2) |
| --- | --- | --- |
| 20 models: App init | 0.214 s / 94.2 MiB peak | 0.651 s / 132.9 MiB |
| 200 models: App init | 0.321 s / 94.7 MiB peak | 1.082 s / 148.2 MiB |
| GLB reads at startup (20 / 200) | 0 / 0 | 0 / 0 |
| Pet windows at startup (20 / 200) | 0 / 0 | 20 / 200 |

Per-frame claims from Copy (2)'s own notes (headless, GL calls recorded, not a
real GPU): 40 → 2 shader programs for 20 models; 115 → 25 uniform uploads per
student per frame; 6.0 → 4.9 ms CPU per frame with 3 students.
