# Merge notes — Copy (2) renderer/loader work into `irojhasda-main`

Everything below was checked against the two working trees, then frozen into
this package:

- base: `irojhasda` remote `main` at
  `0b0ca5239bb84f942cee28fb38c668c315dc510f`, snapshotted in `base/`;
- source: `newest irojhasda-main - Copy (2)`, snapshotted in `copy2/`.

The source checkout was never published as a repo; `copy2/` is the only copy of
it. Treat it as reference material, not as a runnable tree. Line numbers refer to
the snapshots in this folder, which are byte-identical to the two trees at
handoff time. Clone `https://github.com/Singularity1178/irojhasda.git` and edit
that working copy.

---

## 1. Ground rules

1. **`irojhasda-main` is the base.** Do not replace its startup, roster, residency,
   or clip architecture with Copy (2)'s. Copy (2) loads model shells lazily and
   runs a start-up sync for every model; we measured that as worse on this machine.
2. **Port behaviour, not files.** Copy (2)'s `renderer.py` is a near-drop-in, but
   `model.py` and `pet_render.py` are entangled with its lazy `Character`/`Pet`
   shells. Read the diffs (`patches/`) and apply only the parts listed here.
3. **Keep the public surface.** `Renderer(model, fit, ctx=None)`, `set_fit(fit, scale=1.0)`,
   `set_size(w, h)`, `project(p3, M)`, `draw(world, M, light, cfg)`, and the
   attributes in `picker.PreviewEngine.KEYS` must keep working unchanged.
4. **Nothing is a straight copy of a whole module.** If a change here looks wrong
   for the base, say so and explain; this document is a proposal, not a spec.

---

## 2. What to take, in priority order

| # | Change | File | Why |
| --- | --- | --- | --- |
| 1 | Shared shader programs + `_Prog` uniform caching | `bapet/renderer.py` | 40 → 2 programs for 20 models; 115 → 25 uniform uploads per student per frame |
| 2 | `draw(flip_y=..., into=...)` + reusable frame buffer | `renderer.py`, `pet_render.py`, `pet.py` | Removes one full-frame copy and one frame-sized allocation per tick |
| 3 | Per-prim uniform values precomputed at upload | `renderer.py` | Removes Python tuple/float work from every draw |
| 4 | Explicit framebuffer/renderbuffer release in `set_size` | `renderer.py` | Stops stranding ~7 MB per resize per student/ragdoll |
| 5 | Batched channel sampling in the clip baker | `gltf.py`, `model.py` | Fewer numpy calls while baking; bit-identical output |
| 6 | `level_parents` | `model.py` | Removes a per-level parent lookup from every `pose_world` |
| 7 | `Renderer.release()` (optional, separate commit) | `renderer.py`, `app_models.py`, teardown | Only needed if you add a model-unload path; see §6 |

Expected size: roughly 200–250 net new lines, 350–400 touched.

---

## 3. `bapet/renderer.py`

The whole file is in `copy2/bapet/renderer.py` and the diff is `patches/renderer.patch`.
Port it in this order so each step is testable.

### 3.1 Shared programs

Copy (2) adds:

```python
_IDENT = mat_bytes(np.eye(4))
_FLIP_Y = np.diag([1.0, -1.0, 1.0, 1.0])

class _Prog:
    __slots__ = ("prog", "h", "last")
    def __init__(self, ctx, vs, fs): ...
    def handle(self, name): ...   # name -> uniform or None, cached
    def set(self, name, val): ... # scalar/tuple, skipped if unchanged
    def write(self, name, data): ...      # matrices, always sent
    def write_once(self, name, data): ... # identity node matrix

_PROGRAMS = {}
def shared_programs(ctx): ...  # id(ctx)-keyed, identity-checked
```

In `__init__`:

```python
self._pg, self._po = shared_programs(ctx)
self.pg, self.po = self._pg.prog, self._po.prog
```

Keep `self.pg` / `self.po` even though nothing outside `renderer.py` reads them
today; they are cheap compatibility aliases.

**Why sharing is safe:** `draw()` writes every uniform it relies on before each
use, and `_draw()` writes every per-primitive uniform. The cache is per program,
so a second renderer on the same context starts from whatever the first left
behind, but the first `draw()` overwrites all of it.

**Do not** key `_PROGRAMS` by `id(ctx)` alone. Copy (2) stores the context in the
tuple and checks `ent[0] is ctx`, because Python can reuse an `id` after a context
is collected. Keep that check.

### 3.2 Allocation tracking and release

Copy (2) adds `self._gl = []` and `self._fb = []`, collects `bone_tex`, the white
texture, every cached texture, every `vao`/`vao_o`, and every `vbo`/`ibo`.

- `set_size()` frees `self._fb` **before** allocating the new set and then records
  `[fbo, fbo_rs, col, dep, rs]`.
- `release()` frees `_fb + _gl`, nulls `fbo/fbo_rs`, resets `w/h`, and nulls
  `p.tex`, `p.vao`, `p.vao_o` on the model's prims.

Caveats:

- **Keep the base's buffer upload.** Copy (2) writes
  `ctx.buffer(data.tobytes())`; the base uses `ctx.buffer(data)` (buffer protocol).
  Keep `ctx.buffer(data)` and `ctx.buffer(np.ascontiguousarray(p.idx, dtype="u4"))`.
  Same for `ibo`. This is one of the reasons the base is already faster at load.
- **Do not free the shared programs.** `release()` must not touch `_pg` / `_po`.
- **`picker.py` swaps `r.fbo` / `r.fbo_rs` temporarily** (`PreviewEngine._frame`,
  base line ~353) and restores them in `finally`. It does not know about `_fb`.
  That is safe as long as `set_size()` / `release()` are not called while the swap
  is active. They are not today (`_frame` calls `set_fit`, never `set_size`), but
  if you add a release-on-idle path, exclude a character whose renderer is being
  used by the picker.
- **Allocation failure ordering.** Copy (2) updates `self.w, self.h` before it
  builds the new framebuffers. If the allocation raises, the renderer is left
  with the new size and a released/null fbo. Prefer allocating into locals first,
  then freeing the old set, then assigning; if you keep Copy (2)'s order, at least
  restore `self.w, self.h` in the `except`.
- The base's `AppModels.ctx` sets `ctx.gc_mode = "context_gc"`. Explicit release
  is still worthwhile (it stops the deferred set growing on every resize) and is
  idempotent through `_free`'s `try/except`. Do **not** call `ctx.gc()` from a
  frame path.
- `model.img_cache.clear()` is **already** done by `AppModels.upload_student`
  (base `app_models.py:36`). Do not add a second clear inside `Renderer.__init__`.

### 3.3 `draw(..., flip_y=False, into=None)`

Copy (2) changes the signature to:

```python
def draw(self, world, M, light, cfg, flip_y=False, into=None):
```

and builds the projection as:

```python
proj_b = mat_bytes(_FLIP_Y @ self.proj if flip_y else self.proj)
```

then reads:

```python
if into is not None:
    self.fbo_rs.read_into(into, components=4, alignment=1)
    return into
return self.fbo_rs.read(components=4, alignment=1)
```

Caveats:

- `self.PV` and `project()` stay **unflipped**. `project()` returns top-down pixel
  coordinates, and after `flip_y=True` the readback rows are top-first, so the two
  agree. Do not flip `PV`.
- `into` must be at least `w * h * 4` bytes. `read_into` raises if it is short.
  Guard with `len(raw) != w * h * 4`, not just `None`.
- `read_into` needs moderngl ≥ 5.6. The base venv has 5.12.0, so it is present;
  if the target machine has an older moderngl, fall back to `.read()`.
- `draw()` must stay backward compatible: `picker.py` calls it with no kwargs and
  keeps its own `_image()` path. Do not change how picker receives rows.
- Default args must remain `flip_y=False, into=None`.

### 3.4 `_draw()` and the `u_node` reset

```python
q = self._po if outline else self._pg
p.tex.use(0)
q.set("u_skinned", p.u_skinned); q.set("u_base", p.u_base)
if p.u_skinned:
    q.write_once("u_node", _IDENT)
else:
    q.last["u_node"] = None          # <- required
    q.write("u_node", mat_bytes(world[p.node]))
q.set("u_basecol", p.u_basecol)
q.set("u_alpha_mode", p.u_amode); q.set("u_cutoff", p.u_cutoff)
if not outline:
    q.set("u_shade", p.u_shade); q.set("u_fade", float(fade))
```

The `q.last["u_node"] = None` line is **not optional**. Without it, the first
skinned primitive after a non-skinned one would see `last == _IDENT`, skip the
write, and use the non-skinned node matrix from the previous primitive. Copy (2)
has that reset; keep it.

`write_once` compares with `is`, so `_IDENT` must be the same object every call.
Do not replace it with a fresh `mat_bytes(np.eye(4))` per draw.

### 3.5 Per-primitive uniform cache

At upload time, freeze the per-primitive values:

```python
p.u_skinned = 1 if p.skin >= 0 else 0
p.u_base = int(p.base)
p.u_basecol = tuple(float(x) for x in p.basecol)
p.u_amode = int(p.amode)
p.u_cutoff = float(p.cutoff)
p.u_shade = float(p.shade)
```

This is safe **today** because `model.py` assigns `shade`, `cutoff`, `basecol`,
`amode`, `outline` only during construction. If you later add a runtime tweak
(e.g. a settings slider that changes a prim's shade), invalidate `p.u_shade`
explicitly or read it live.

### 3.6 `IDENTITY_BYTES`

The base has a module-level `IDENTITY_BYTES`. Copy (2) removes it in favour of
`_IDENT`. Either name is fine, but there should be exactly one, and `write_once`
must receive that same object each time.

---

## 4. `bapet/gltf.py` + `bapet/model.py`

Only two small things come out of `gltf.py`; the rest of Copy (2)'s changes there
are part of its lazy-clip/loader rewrite and should stay out (see §7).

### 4.1 Add `sample_plan` / `sample_channels`

Copy (2) `gltf.py:206-225`, also in `patches/gltf.patch`:

```python
def sample_plan(times, ts):
    idx = np.clip(np.searchsorted(times, ts, side="right") - 1, 0, len(times) - 2)
    t0, t1 = times[idx], times[idx + 1]
    u = np.clip((ts - t0) / np.maximum(t1 - t0, 1e-9), 0, 1)[:, None]
    return idx, u

def sample_channels(vals, idx, u, step, rotation):
    if step: u = np.zeros_like(u)
    a, b = vals[:, idx], vals[:, idx + 1]
    if rotation: return slerp(a, b, u)
    return a + (b - a) * u
```

`vals` is `(channels, keys, dim)`; the result is `(channels, frames, dim)`.
`slerp` in `mathutil.py` already broadcasts over the leading channel axis
(`q0 * q1` sums over `axis=-1`), so no change is needed there.

**Everything must be bit-for-bit the old result.** `sample_channel` in the base
already strips CUBICSPLINE tangents (`vals = vals[1::3]`, line 168) and then does
linear/step interpolation; `sample_channels` does not strip, so the caller must.
That is exactly what Copy (2) does before grouping.

### 4.2 Rewrite the inner loop of `Model._bake_clip`

Keep the base signature `_bake_clip(self, name, a)` — `DeferredClip` calls it with
`(name, animation)`; Copy (2)'s `(c, a)` signature belongs to its lazy `Clip` and
must not be adopted.

Add to `Model.__init__`:

```python
self._times = {}   # time accessor index -> float64 key times, shared by channels
```

and:

```python
def _key_times(self, idx):
    t = self._times.get(idx)
    if t is None:
        t = self._times[idx] = self.g.accessor(idx).astype(np.float64).reshape(-1)
    return t
```

Then replace the `chans` list + per-channel loop with Copy (2)'s grouping:

```python
dest = {"translation": cT, "rotation": cQ, "scale": cS}
plans = {}    # time accessor -> (idx, u)
groups = {}   # (time accessor, is_step, path, vals.shape) -> ([nodes], [vals])
for ch in a["channels"]:
    ...
    times = self._key_times(sm["input"])
    vals = g.accessor(sm["output"]).astype(np.float64)
    if path == "weights":
        # keep the base's single-channel call
        ...
        continue
    if interp == "CUBICSPLINE" and len(vals) == 3 * len(times):
        vals = vals[1::3]
    if len(times) < 2 or len(vals) != len(times) or vals.ndim != 2:
        dest[path][:, node] = sample_channel(times, vals, interp, ts, path)
        continue
    key = (sm["input"], interp == "STEP", path, vals.shape)
    grp = groups.setdefault(key, ([], []))
    grp[0].append(node); grp[1].append(vals)

for (inp, step, path, _shape), (nodes, vals) in groups.items():
    plan = plans.get(inp)
    if plan is None: plan = plans[inp] = sample_plan(self._key_times(inp), ts)
    v = sample_channels(np.stack(vals), plan[0], plan[1], step, path == "rotation")
    dest[path][:, nodes] = v.transpose(1, 0, 2)
```

Preserve the base's float32 destinations:

```python
cT = np.repeat(self.rest_T.astype(np.float32)[None], F, 0)
cQ = np.repeat(self.rest_Q.astype(np.float32)[None], F, 0)
cS = np.repeat(self.rest_S.astype(np.float32)[None], F, 0)
cW = np.repeat(self.node_w0.astype(np.float32)[None], F, 0)
```

Copy (2) drops the `.astype(np.float32)` because its `rest_*` are already float64
and it lets the baked clips be float64. That is the "float64 baked tracks" change
we explicitly do **not** want: it doubles memory and changes the established
numerics. Assigning a float64 batch into a float32 `dest` casts on assignment,
so the values match the old per-channel loop.

Keep **all** of the base's post-bake work exactly as is:

```python
P = pelvis_track(self.pelvis_chain, c.T, c.Q, c.S)
c.drift = ...
c.pin = ...
c.ssig = ...
c.origin = self._pivot - P[0]        # base has _pivot / _idle_ref
# kneel floor correction ...
return c
```

Copy (2) replaced this with `_origin_ref()` / `_floor()`; do not port that. The
base's `_pivot` / `_idle_ref` are set up differently and `DeferredClip` assumes it.

### 4.3 `level_parents`

After `self.levels = [...]` in `Model.__init__`:

```python
self.level_parents = [self.parent[idx] for idx in self.levels]
```

In `pose_world()`:

```python
lp = self.level_parents
for d, idx in enumerate(self.levels):
    if d == 0: W[idx] = L[idx]
    else: W[idx] = np.matmul(W[lp[d]], L[idx])
    if not pm: continue          # <- add this, keeps post-transforms correct
    for node, R in pm.get(d, ()): ...
```

`lp[d]` is an integer array of the same length as `idx`, so `W[lp[d]]` has the
same shape as `W[self.parent[idx]]`. This is a pure lookup hoist.

---

## 5. `bapet/pet_render.py` + `bapet/pet.py`

`patches/pet_render.patch` is the entire change (about 6 lines):

```python
raw = self._fbuf
if raw is None or len(raw) != w * h * 4:
    raw = self._fbuf = bytearray(w * h * 4)
R.draw(world, M, self.light, cfg, flip_y=True, into=raw)
self.raw = raw
self.img = QtGui.QImage(raw, w, h, w * 4, QtGui.QImage.Format.Format_RGBA8888_Premultiplied)
self.img.setDevicePixelRatio(self.dpr)
self.alpha = np.frombuffer(raw, np.uint8).reshape(h, w, 4)[:, :, 3]
```

Also add `self._fbuf = None` to `Pet.__init__` next to
`self.img = None; self.alpha = None; self.raw = None` (base `pet.py:137`).
The base has only that one reset location; Copy (2) also has a second one at
`pet.py:226` because its lifecycle is different.

Caveats:

- **Alpha orientation changes together with `flip_y`.** `self.alpha` must become
  `[:, :, 3]`, not `[::-1, :, 3]`. It stays top-first either way, which is what
  `pet_input.py` / `app_hotkey.py` hit-testing expects. Change the two lines
  together or the mask is upside down.
- **`QImage` aliases the reusable bytearray.** Without `.mirrored()` there is no
  Qt-owned copy; the image is a view over `raw`, and the next frame overwrites it.
  That is intended (the widget repaints from `self.img` synchronously), but do not
  cache `self.img` anywhere that outlives the next `render()`. If you see torn
  frames on a slow/queued paint, `self.img = self.img.copy()` is the safe fallback
  at the cost of the copy we just removed.
- `np.frombuffer(raw, ...)` works on `bytearray` (read-only view). Do not write to
  `self.alpha`.
- `R.project(...)` still uses the unflipped `PV`; it returns top-down coordinates,
  which now match the top-first image. Leave it alone.

---

## 6. Optional: `Renderer.release()` and an unload path

Take this as a **separate commit after** the performance port is green.

`irojhasda-main` has no `Character.unload()`; residency is bounded by
`ModelPool`'s weak live map + two warm entries, and GPU memory is left to
`context_gc`. Copy (2)'s `release()` is only useful if you also add a
deterministic unload.

If you do add it:

1. `Renderer.release()` as in Copy (2), plus the `_gl` / `_fb` tracking from §3.2.
2. Call it from a real teardown point, e.g. when `ModelPool` evicts a warm
   character or when a pet is recalled for good. Do not release a renderer that
   the picker currently has swapped in (`PreviewEngine._frame`).
3. Null `Character.renderer` and drop the model reference only after
   `release()` returns; `release()` loops `self.m.prims`.
4. Do not release shared programs.
5. Rebuilding a renderer for the same `Model` is fine: `__init__` re-uploads
   VAOs and textures. It will need the decoded images again, so either keep
   `img_cache` or re-decode; today `upload_student` clears it after upload.
   That is a real tradeoff: if you unload/reload often, keep the decoded RGBA
   for recently used models or accept the re-decode.

If none of that is needed, skip `release()` entirely. It is not required for the
per-frame wins.

---

## 7. Do **not** port

| Copy (2) thing | Why not |
| --- | --- |
| Lazy `Character` / `Pet` shells (`character.py`, `pet.py`, `app_pets.py`) | The base already has the better split: metadata roster + `ModelPool` single-flight decode + no widgets at startup. Copy (2) builds one `Pet` QWidget per model (20/200 windows at startup) and issues a start-up sync for every model. |
| `start_background_sync()` / per-entry voice work at startup | Same reason; the base already synchronises only deployed pets. |
| `model_cache` policy / `AppPets.trim_models` | The base's weak-live + 2-warm policy is simpler and measured better. |
| Portrait cache on disk (`cache/portraits`, `portraits.py`) | Out of scope for this port; the base ships a portrait atlas and its own preview path. |
| Lazy `gltf.Clip` (`_baker`, `__getattr__`, `baked`) | The base has `DeferredClip`, which achieves the same laziness at the model layer. Do not end up with two lazy clip systems. |
| Float64 baked tracks (`_sample_clip` without `.astype(np.float32)`) | Doubles track memory; changes established numerics. |
| Strong global `_BINDINGS` in `babattlearena/physics.py` | Turns a weak cache into a leak of every past opponent; the base deliberately made it weak. |
| `GLTF.__init__` chunk-parsing rewrite (memoryview BIN) | The base already reads chunks separately and avoids the second whole-file copy (PERFORMANCE.md). The rewrite also drops the base's stricter header/chunk validation. Measure before touching. |
| `GLTF.accessor` strided gather rewrite | The base reads strided accessors as NumPy views; Copy (2) builds an integer gather matrix. That is slower and was only needed for its contiguous/`.view` handling. |
| `foreign_image` unbounded dict | The base's `@lru_cache(maxsize=4)` is bounded; `Model.get_image` already caches per model. Not a hot path. |
| `picker.py` Copy (2) rewrite | Large, and only the call-site compatibility matters here. Keep the base's picker. |

---

## 8. Integration touch points

- `bapet/app_models.py:34-36` — `upload_student()` builds the renderer and clears
  `img_cache`. No change needed for the port.
- `bapet/app_models.py:15-23` — the shared context is created lazily and has
  `gc_mode = "context_gc"`. Keep it.
- `bapet/picker.py:333-366` — the only other `draw()` caller. It saves/restores
  `fbo, fbo_rs, w, h, fit, ...` but not `_fb`. Do not call `set_size` /
  `release` from inside `_frame`, and do not add `_fb` to its saved keys unless
  you also teach it to restore the old list correctly.
- `bapet/pet.py:137` — add `_fbuf = None`.
- `bapet/renderer.py` — the only file that should grow substantially.
- `PERFORMANCE.md` — after the port, update the per-frame section with the new
  claims (shared programs, 25 uploads/student/frame, single readback, reusable
  buffer, renderbuffer release). Do not copy Copy (2)'s doc wholesale; it
  describes lazy shells and a portrait cache that the base does not have.

---

## 9. Verification

Run from the cloned `irojhasda` root with its venv (the repo ships no
`requirements.txt`; the verified set is PySide6 6.11.2, moderngl 5.12.0,
numpy 2.5.3, pymunk 7.3.0 on Python 3.13):

```powershell
cd irojhasda
python -m venv venv
.\venv\Scripts\pip install PySide6==6.11.2 moderngl==5.12.0 numpy==2.5.3 pymunk==7.3.0

# 1. loader/residency regressions
.\venv\Scripts\python.exe -m unittest -v test_model_loading

# 2. live 200-entry integration flow (real GLB reads, textures, rotation)
.\venv\Scripts\python.exe test_asset_runtime.py --live

# 3. startup must not regress
.\venv\Scripts\python.exe tools\benchmark_startup.py --root . --models 20 --runs 3
.\venv\Scripts\python.exe tools\benchmark_startup.py --root . --models 200 --runs 3
```

On Linux, use `venv/bin/python` and prefix the live test with `xvfb-run -a`.

Then add a small bake-equality test (do **not** drop in
`tests/test_lazy_models.copy2.py` unchanged — it asserts Copy (2)'s lazy
`Character`, which the base does not have). The useful half is
`reference_bake()`: take one model, compare `_bake_clip` output against a
channel-by-channel reference for the first 6 animations, with
`np.array_equal`, and also assert `c.T.dtype == np.float32`.

Manual smoke test on Windows (real GL):

1. `.\run_any_py.bat` / the normal launcher; deploy two students.
2. Walk, poke, pat, ragdoll, and resize the pet window with the wheel — no
   visual flip, no alpha/mask offset, no crash.
3. Open Student Select and scroll a long roster; previews must not be upside
   down and must not double-read.
4. Enter a battle / arena and return; no black frames, no stale uniforms.
5. Watch GPU memory across repeated resizes if you can (Task Manager /
   `nvidia-smi`): it should plateau instead of climbing per notch.

Because `benchmark_startup.py` uses a headless stand-in for moderngl, it will
**not** show the renderer gains. It only proves you did not regress startup. Use
the real app for the per-frame claims, or a small standalone-context harness that
draws one model N times and measures `draw()` wall time.

---

## 10. Commit hygiene

A fresh clone is clean. Follow the repo's `AGENTS.md`:

- stage only the files you actually changed (`bapet/renderer.py`,
  `bapet/gltf.py`, `bapet/model.py`, `bapet/pet_render.py`, `bapet/pet.py`,
  optionally `bapet/app_models.py`, `PERFORMANCE.md`, and the new test);
- do not commit `multi_single_screen_pet_ba_settings.json`,
  `battle_arena_settings.json`, `campaign_save.json`, or `__pycache__`;
- push after committing and confirm the branch is not ahead. If the clone has no
  push credentials, keep the commit and report that it is local-only.
