# On-demand model loading

Base revision: `51526e1fd58468a6aca2ca7eb0f9e71a2247b294`.
This is a changed-files overlay, not a standalone game distribution.

## Install

Close the app. Back up your checkout, especially uncommitted edits. Extract
the ZIP into the directory that contains `multi_single_screen_pet_ba.py`
and the existing `.bat` launchers. Merge directories and overwrite matching
files. Do not delete or replace whole directories. There are no removals,
renames, model conversions, new dependencies, or changes to the launch commands.
For a different base revision, review the changes before merging.

The archive does not contain GLBs, virtual environments, saved positions,
campaign saves, arena settings, downloaded dialogue, or the shop's assets.
For rollback, restore your backup, including the original settings if you
want the exact prior formation. The download page lists added files separately.

## What was expensive

The old `bapet/app.py` constructed a `Character`, `Renderer`, and `Pet` for
every discovered model, even when starting directly in campaign or arena.
`Model.__init__` expanded every animation to dense frame-by-node arrays.
Hiding the windows did not release any of those allocations. The three
selection screens then scheduled portraits for the entire catalog, and the
preview path read every rendered frame back from the GPU twice.

The stock repository has 20 student GLBs with 571 clips, plus a separate
texture-donor model. Donor files are not independent students.

## Runtime behavior

- Startup indexes paths, names, and profiles. It does not open model payloads,
  start a model-loading worker job, create a GL context, or create pet windows.
  The explicit diagnostic `--dump` is the exception: it inspects all requested
  models sequentially. `--model` files, folders, duplicates, and `pet=False`
  profiles remain supported.
- Campaign and arena browse the same lightweight metadata and 2D portraits.
  Beginning a room/match loads its unique living participants; repeated
  enemies share the same character data. Each fighter still has its own rig,
  state, and pose. No desktop window is needed for a fighter.
- Desktop selection loads only visible formation previews. Scrolling a roster
  card into view never causes a GLB decode. Deployment shares the preview's
  character if available, and creates a window only for an actual pet. A
  recalled pet's position is kept; its window and transient simulation state
  are retired. Switching to battle also retires desktop windows. Returning
  restores the previously visible students and their positions.
- Fresh installations start with one suggested student, not the whole
  library. Existing saved positions and hidden names seed the migration to
  an explicit `deployed` list. New files appear in the roster without being
  silently deployed. An explicitly empty team stays empty. `--no-picker`
  restores the saved formation rather than loading the entire catalog.
- The launcher no longer synchronizes voice text for the entire library.
  Deployed pets use one deduplicated background voice worker. Cached dialogue
  and the existing downloader still work as before.

## Cache and rendering choices

`bapet/assets.py` separates metadata from residency. One worker decodes a
requested GLB, prepares used geometry, and decodes textures. GPU uploads,
QWidgets, and rendering stay on the owning GUI thread. The selection dialog
stays responsive and can be canceled. Abandoned preview work is never
uploaded just to fill a cache. A corrupt student does not prevent browsing
the others; an explicit retry can load a repaired file.

Active consumers own strong references. A weak lookup avoids duplicate
decodes, and an LRU keeps at most **two additional warm characters**. This
is an object-count bound, not a fixed byte budget: huge assets, a large
active team, temporary uploads, and one in-flight CPU decode still cost
memory. GPU resources are collected on the context thread (`context_gc`).
Ragdoll bindings are weakly cached instead of retaining every past opponent.

The shipped portrait atlas contains actual renderer output for the original
20 models. Stock names and sizes select a built-in preview. New models use
their initials until their first live formation preview; that preview writes
a small PNG to the OS cache directory under `irojhasda/portraits-v1`. Keys
include absolute path, file size, nanosecond modification time, and profile.
A maximum of 48 decoded portraits and 256 generated disk portraits are kept.
The bundled atlas itself is shared. Delete the OS portrait cache to regenerate
custom portraits. It is safe to run with a read-only cache directory.

Clip names remain immediately available for role/attack discovery; actual
frame arrays are built when a clip's data is used. Dense baked tracks use
float32, while source key interpolation and the existing pose/physics logic
are retained. The idle reference, pelvis pinning, kneeling alignment,
morph weights, profile overrides, and Iroha's riding setup are preserved.
Initial fits are reused instead of skinning the same reference pose twice.

GLB chunks are read separately, avoiding a second whole-file binary copy.
Interleaved accessors use strided NumPy views instead of a vertex-by-byte
integer gather matrix. Normalized accessors perform only the applicable
conversion. Texture CPU staging is discarded after upload. VBO/IBO data
uses the buffer protocol. Each live preview needs one resolve/readback,
not two. Idle menus skip desktop simulation and reduce its timer frequency.
The cast control panel builds only 12 detailed rows at a time.

## Measured results

Linux, Python 3.13.18, PySide6-Essentials 6.12.0, ModernGL 5.13.0,
Mesa llvmpipe, Xvfb, one OpenBLAS thread. Median of three fresh processes.
The matched four-model sample was Ako, Arisu, Ayane, and Azusa.

| App initialization | Base, 4 files | Patched, 4 files | Patched, 20 files |
| --- | ---: | ---: | ---: |
| Time | 2.3918 s | 0.1691 s | 0.1567 s |
| Peak process RSS | 449.4 MiB | 104.7 MiB | 105.2 MiB |
| GLB reads | 4 | 0 | 0 |
| Pet windows constructed | 4 | 0 | 0 |

These timings exclude interpreter/import time, splash, native tray/hooks,
sound, and network. They include App setup, including its model work. They
are not full launch times, VRAM measurements, or FPS guarantees. Small time
differences between patched sample sizes are run-to-run variation. Loading
cost is deferred, not eliminated: first use still has to prepare the asset.
The unchanged 20-model startup was not timed as a single process.

In a separate Ako decode check, the dense T/Q/S/morph tracks allocated at
construction fell from 21.27 MiB (8 clips) to 0.55 MiB (1 clip). This is
animation storage only, not total character memory.

## Verification

```sh
python -m unittest -v test_model_loading
python test_asset_runtime.py --live
python test_campaign.py --battle --live
python test_arena_ai.py
python -m unittest discover -s dialogue/tests
python tools/benchmark_startup.py --root . --models 4 --runs 3
```

Use the repository's Python 3.13 environment. Run the GL integration test
and benchmark with a graphics session (`xvfb-run -a` on headless Linux).
To compare a clean base checkout, point the same benchmark's `--root` at it.
The benchmark includes import-only compatibility shims for the old source on
Linux. It does not change the base checkout.

The 200-entry integration test uses the original models and separately named
links/copies of a real GLB. It exercises actual Qt windows and OpenGL uploads,
not a mock renderer. It verifies zero GLB reads, zero pet windows, and no GL
context across the title, arena lobby, and campaign hub. It browses all 200
portraits, previews and deploys a custom model, enters a two-student match,
starts a campaign room, restores the desktop, and checks cache residency
after recall. No network requests or personal saves are used. This is not a
claim about rendering 200 simultaneous pets or 200 distinct rigs at once.

All 571 clips across the stock 20 models were compared against the base:
clip names, durations, root-motion presence, sampled pelvis pins, origins,
model fits, and final pelvis transforms. Tolerance was `rtol=2e-5, atol=1e-5`;
the largest final-transform absolute difference was approximately `1.15e-6`.
This is a sampled numerical comparison, not a pixel-perfect proof for every
frame. The campaign simulation/live UI, arena AI, loader, dialogue, and
real-rig checks were also run.

Native Windows hooks, audio, the optional QtWebEngine shop, and performance
on a physical GPU were not verified here. Four existing `test_ragphys.py`
checks also failed in the original checkout with the available pymunk 7.3.1
environment: window-created-on-top displacement, the timing budget, a
settled-pelvis angle, and collision-group expectations. The patch does not
modify the physics solver or relax these assertions.