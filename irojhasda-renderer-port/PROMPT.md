# Prompt — port the renderer/animation wins from Copy (2) into `irojhasda-main`

You are working on the **irojhasda** Python desktop-pet project (PySide6 + moderngl,
Windows, Python venv in the checkout). Two checkouts exist:

- **Target / base:** `irojhasda-main` — the one to change.
- **Source:** `newest irojhasda-main - Copy (2)` — an experimental optimisation
  branch. Its relevant files are also snapshotted in the GitHub folder below.

Reference package:
**https://github.com/Singularity1178/things/tree/master/irojhasda-renderer-port**

Read, in this order:

1. `README.md` — what is in the package and the measured verdict.
2. `MERGE_NOTES.md` — file-by-file plan, exact traps, do-not-port list.
3. `patches/renderer.patch`, `patches/gltf.patch`, `patches/model.patch`,
   `patches/pet_render.patch` — mechanical diffs (`git apply -p1` from a repo root).
4. `base/bapet/*.py` and `copy2/bapet/*.py` — the two snapshots being compared.
5. `docs/PERFORMANCE-main.md` and `docs/PERFORMANCE-copy2.md` — each side's claims.

If you cannot find the two checkouts, locate them or ask; do not invent files.

---

## The decision you are implementing (already measured — don't redo it)

`irojhasda-main` is the better overall architecture and stays the base:

- zero GLB reads, zero GL context, zero pet windows at startup;
- single-flight decode on a worker, GPU upload only on the owner thread;
- bounded residency (weak live map + 2 warm characters);
- `DeferredClip` clips and float32 baked tracks.

Copy (2) is better **inside the per-frame renderer**, and slightly better in the
animation baker. On the same machine:

| 20 models, App init | main | Copy (2) |
| --- | --- | --- |
| wall time | 0.214 s | 0.651 s |
| peak RSS | 94.2 MiB | 132.9 MiB |

Copy (2)'s own headless numbers for the renderer: 40 → 2 shader programs for 20
models, 115 → 25 uniform uploads per student per frame, 6.0 → 4.9 ms CPU per
frame with 3 students.

So: **keep the base's startup/roster/residency design, port Copy (2)'s
per-frame rendering layer and its batched channel sampler.**

---

## Scope — what to port

### 1. `bapet/renderer.py` (the main change, ~90% compatible)
- `_Prog`: cache uniform handles and skip unchanged values.
- `shared_programs(ctx)`: one shader pair per GL context, shared by every renderer.
- Precompute per-primitive uniform values at upload (`p.u_skinned`, `p.u_base`,
  `p.u_basecol`, `p.u_amode`, `p.u_cutoff`, `p.u_shade`).
- `draw(world, M, light, cfg, flip_y=False, into=None)`:
  - `flip_y` multiplies the projection by `diag(1,-1,1,1)` so readback rows come
    back top-first;
  - `into` uses `fbo_rs.read_into(...)` and returns the same buffer.
- Track every GPU object (`_gl`) and the current framebuffers (`_fb`); free the
  old set in `set_size`, including the **renderbuffers attached to the
  framebuffers** (releasing a framebuffer does not free its attachments).
- Optional, separate commit: `release()`. Only if you add a real unload path.

**Keep from the base:** the constructor signature, `self.pg`/`self.po`, the
buffer-protocol VBO/IBO upload (`ctx.buffer(data)`, not `data.tobytes()`), the
`context_gc` context, and `AppModels.upload_student`'s `img_cache.clear()`.

**Must keep the trap fix:** in `_draw`, non-skinned prims need
`q.last["u_node"] = None` before writing the real node matrix. Without it a later
skinned prim skips its identity write and inherits the wrong matrix.

**Must stay consistent:** `self.PV` / `project()` are *not* flipped. `project()`
returns top-down coordinates; after `flip_y=True` the image is top-first, so they
match. Do not "fix" one without the other.

### 2. `bapet/gltf.py`
Add `sample_plan(times, ts)` and `sample_channels(vals, idx, u, step, rotation)`.
That is all. Do not port lazy `Clip`, the `foreign_image` dict, the GLTF chunk
parser rewrite, or the accessor/normalized rewrites — see the do-not-port table.

### 3. `bapet/model.py`
- Add `self._times = {}` + `_key_times(idx)` (cache decoded key times per
  accessor index).
- Rewrite the inner loop of `_bake_clip` to group channels that share a time
  accessor and sample them in one `sample_channels` call. Keep the signature
  `_bake_clip(self, name, a)`, because `DeferredClip` calls it that way.
- Preserve **float32** destinations (`cT/cQ/cS/cW` initialised with
  `.astype(np.float32)`); the batch result is float64 and casts on assignment.
- Strip CUBICSPLINE tangents (`vals = vals[1::3]`) before grouping, exactly as
  Copy (2) does; `sample_channels` does not do it.
- Fall back to `sample_channel` for `len(times) < 2`, shape mismatches, and
  weights channels.
- Keep **all** of the base's post-bake logic (`pelvis_track`, drift, pin, ssig,
  `_pivot`, kneel floor correction). Copy (2) replaced it; do not adopt that.
- Add `self.level_parents = [self.parent[idx] for idx in self.levels]` and use it
  in `pose_world`; keep `if not pm: continue` so post-transforms still run.

### 4. `bapet/pet_render.py` + `bapet/pet.py`
- Reuse one `bytearray` frame buffer (`self._fbuf`), size `w*h*4`.
- `R.draw(..., flip_y=True, into=raw)`.
- Drop `.mirrored(False, True)` on the QImage.
- Change `alpha` from `[::-1, :, 3]` to `[:, :, 3]` — the two changes are a pair.
- Add `self._fbuf = None` next to `self.img/alpha/raw = None` in `Pet.__init__`.

---

## Explicitly out of scope (do not merge)

- Copy (2)'s lazy `Character` / `Pet` shells and `AppPets` sync/trim policy.
- Per-model `Pet` QWidgets at startup, `start_background_sync()` for everything.
- Portrait disk cache (`cache/portraits`, `portraits.py`).
- `model_cache` setting.
- Lazy `gltf.Clip` (`_baker` / `__getattr__` / `baked`) — the base already has
  `DeferredClip`; two laziness systems would fight.
- Float64 baked tracks.
- Strong global `_BINDINGS` in `babattlearena/physics.py`.
- A wholesale `picker.py` rewrite. It only needs to keep working unchanged.

Each of these is justified in `MERGE_NOTES.md §7`. If you think one of them is
actually required for the port to work, stop and explain before doing it.

---

## Constraints

- Keep `Renderer.draw()` backward compatible: `picker.py` calls it with no
  keyword arguments and consumes bottom-up rows via its own `_image()`.
- `picker.PreviewEngine._frame` temporarily assigns `r.fbo` / `r.fbo_rs` and
  restores them. Do not call `set_size()` or `release()` while that swap is live,
  and do not add `_fb` to its saved keys unless you also restore the old list.
- `read_into` needs moderngl ≥ 5.6 (the venv has 5.12). Add a fallback if you
  care about older installs.
- Do not mutate `p.shade` / `p.cutoff` / `p.basecol` at runtime after caching them.
- Keep changes consistent with the surrounding code: same naming, same terse
  comment style, no new dependencies.
- The working tree is dirty. Stage only the files the change touched. Do not
  commit `multi_single_screen_pet_ba_settings.json` or `__pycache__`.
- Follow the repo's `AGENTS.md`: commit, then push, and confirm the branch is not
  ahead.

---

## Verification (must pass before you call it done)

From the base repo root, with its venv:

```powershell
.\venv\Scripts\python.exe -m unittest -v test_model_loading
.\venv\Scripts\python.exe test_asset_runtime.py --live
.\venv\Scripts\python.exe tools\benchmark_startup.py --root . --models 20 --runs 3
```

Then:

- Add a bake-equality test: for one model and the first 6 animations, compare
  `_bake_clip` output to a channel-by-channel `sample_channel` reference with
  `np.array_equal`, and assert `c.T.dtype == np.float32`. (You can adapt
  `reference_bake()` from `tests/test_lazy_models.copy2.py`, but that file as a
  whole tests Copy (2)'s lazy `Character`, which the base does not have.)
- Manual smoke test in the real app: deploy two students, walk/poke/pat/ragdoll,
  resize with the wheel, open Student Select and scroll, enter and leave a
  battle. Look for: vertical flips, alpha/mask offset, black frames, stale
  uniforms, crashes.
- `benchmark_startup.py` uses a headless moderngl stand-in, so it proves only
  that startup did not regress. The renderer gains need a real context / the app.

---

## Deliverable

A working port in `irojhasda-main`, with:

1. the code changes above;
2. a short note in `PERFORMANCE.md` (update the per-frame section; do not paste
   Copy (2)'s doc — it describes features the base does not have);
3. the verification results;
4. a commit (pushed) that lists what was ported and what was deliberately left out.

---

## You are free to disagree

This plan is a proposal from someone who compared the two trees and measured
them once. You have the actual code and the ability to run it. If you find that:

- a "win" here is not a win on this code path,
- a caveat is wrong or already handled,
- a simpler port achieves the same result,
- or one of the do-not-port items is actually required,

say so **before** implementing the alternative, with the reason (a measurement,
a failing test, a code reference), and propose the change. Do not silently
diverge from the plan, and do not silently follow it if you believe it is wrong.

The goal is a faster, correct renderer on the base architecture — not a faithful
copy of Copy (2).
