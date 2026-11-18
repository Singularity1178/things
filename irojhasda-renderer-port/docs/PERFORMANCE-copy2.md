# Performance: models load on demand

The app used to read **every** `.glb` in `model/` at start-up: parse it, bake all of its
animations to 60 fps, decode and upload every texture, compile two shaders and build a window
for it. That is fine for 5 models, slow for 20 and impossible for 200. Now nothing is read
until it is needed, and what is no longer needed is let go of again.

## What you will notice

| 20 models in `model/`                         | before            | now                       |
|-----------------------------------------------|-------------------|---------------------------|
| start-up (CPU side only, see the note below)   | 14.0 s            | 0.14 s                    |
| memory after start-up                          | +1540 MB          | +20 MB                    |
| models read at start-up                        | 20                | 0                         |
| shader programs compiled                       | 40                | 2 (shared, on first use)  |
| memory with 3 students on the desktop          | 1635 MB           | 243 MB                    |
| uniform uploads per student per frame          | 115               | 25                        |
| time per frame, 3 students (CPU side)          | 6.0 ms            | 4.9 ms                    |

With a **200-model** roster (the 20 models linked ten times over): start-up 0.38 s and +34 MB,
Student Select opens in 0.2 s, one model in memory. The old structure extrapolates to well over
two minutes and ~15 GB for the same folder.

*Note on the numbers.* They were measured headless (Qt `offscreen`, a stand-in for moderngl that
records the GL calls instead of making them), on the real model files. So they are the Python /
numpy side of the work, which is where the time went; real shader compiles and texture uploads
come on top of the "before" column and are skipped just the same in the "now" column. The
relative picture is what matters, not the milliseconds.

## How it works

**A roster of shells.** `App` still creates one `Character` and one `Pet` per file, so every list
in the app (Student Select, the arena lobby, the panel, the saved settings) is unchanged. But both
are shells: a name, the profile from `<name>.py`, the stats from `combat_index.json`. None of that
needs the `.glb`.

**The file is read on first touch** (`bapet/character.py`). `ch.model`, `ch.clips`, `ch.fit`,
`ch.renderer` and friends load the file the first time anything reads them, so no caller had to
change to "ask first":

* **Desktop Pets** - a student loads when she is shown (`Pet.setVisible` -> `ensure_live`). With
  the title screen / Student Select that is when you press DEPLOY; with `--no-picker` last
  session's team is read under the loading card.
* **Student Select** - the students on the stage load one per tick (a spinner stands in). The
  roster cards never load anything: see portraits below.
* **Battle Arena / Campaign** - `Fighter` reads `ch.model`, so exactly the students in the fight
  are loaded, and they are pinned (`ch.pin()`) until `Battle.stop()`.

**Clips bake when they are first played** (`gltf.Clip`, `Model._bake_clip`). Baking was two
thirds of a model's load time and almost all of its memory (Iroha: 36 clips, 110 MB) for clips
that are mostly never played. A clip is now a name and a length until its data is read. The
baker also samples all the channels that share a time track in one numpy call instead of one
call per channel. The baked numbers are bit-for-bit the old ones (`test_lazy_models.py` checks).
A student on the desktop has her everyday clips baked ahead of use, one every few frames
(`AppPets.warm_clips`), so the first walk or poke does not pay for it on that frame.

**Unused models are unloaded** (`AppPets.trim_models`, every 2 s). A model is "in use" while its
student is visible, pinned by a battle, on the Student Select stage, or parked by the arena /
"hide all". Of the rest, the `model_cache` most recently used stay in memory (default 6, so
recall-and-redeploy or a rematch is instant) and older ones are dropped: numpy data by losing its
last reference, GPU objects by `Renderer.release()` - moderngl does not free those by itself.

    "model_cache": 6     in multi_single_screen_pet_ba_settings.json
                         0 = unload as soon as unused, -1 = never unload

**Portraits are cached on disk** (`cache/portraits/<model>.png`). A roster card needs a picture,
and the picture used to be rendered from the loaded model on every run. It is now saved the first
time it is drawn. With a cold cache the cards on screen are filled in first (one model per tick,
loaded, photographed, unloaded) and the rest follow while the mouse is idle: 200 portraits took
44 s once, in the background, and 0.9 s on every later run. A portrait is redone automatically
when its `.glb` or its profile `.py` changes. Deleting `cache/` is always safe.

## Per-frame work that went away

* **Shaders are shared.** Every renderer compiled its own identical pair; there is one pair per
  GL context now (`renderer.shared_programs`).
* **Uniforms are sent when they change.** Handles are looked up once and a value that is already
  set is skipped (`renderer._Prog`): 115 -> 25 uploads per student per frame.
* **The frame is not copied twice.** GL hands rows back bottom-first, so each frame was read into
  a new `bytes` object and then turned over with a second full copy (`QImage.mirrored`, which Qt
  has also deprecated). It is now rendered already the right way up (`draw(flip_y=True)`) and read
  into the pet's own reusable buffer (`into=`), which the QImage and the alpha mask simply view.
  The arena's top-down renderer loses one of its two copies the same way.
* **Student Select read every stage student back twice per tick** (once inside `draw`, once
  more by hand). Once now.
* **Decoded textures are dropped after upload** (they were kept in RAM for the life of the model).
* `stack_order` searched a list per pet per frame (n^2: nothing at 20, 40 000 compares a frame at
  200); `pose_world` re-derived each level's parent indices every call.

## Leaks fixed on the way

* **GPU memory on every resize.** `Renderer.set_size` released the old framebuffers but not the
  renderbuffers attached to them, so each wheel notch (per student - scale is global) and each
  ragdoll (her window grows, then shrinks back) stranded ~7 MB or more of VRAM.
* **GPU memory after every battle.** `TopDownRenderer.release` had the same omission (~9 MB).
* **"Always on top" and "hide all / show all" brought the whole roster up.** Both re-showed every
  student, deployed or not; they now only touch who was actually out.
* Two start-up crashes that only showed off Windows / without a system tray (the panel read
  `App.fps` before it was set; `QShortcut` moved to `QtGui` in Qt 6).

## If you add 200 models

Nothing to configure. The first time Student Select or the arena lobby is opened it will spend a
while filling in portraits in the background (the cards you are looking at come first); after
that it is instant. Deploying a student costs her load at that moment (0.1 - 0.4 s each here),
which is the time that used to be paid for everybody at start-up.
