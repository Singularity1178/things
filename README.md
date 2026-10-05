# Calamitas Desktop Pet

A stand-alone Supreme Calamitas boss fight on your desktop, built from the
official Calamity Mod assets. She chases your cursor and throws her
pre-brothers moveset at it.

## Two versions — pick one

| Launcher | Module | Fight logic |
| --- | --- | --- |
| **`run_pet_ai.bat`** | `calamitas_pet_ai.py` | **Ported from the mod source.** Attack selection, timings, projectile speeds/lifetimes/behaviour and her movement all trace to `SupremeCalamitas.cs` and the projectile classes. |
| `run_pet.bat` | `calamitas_pet.py` | The earlier hand-built version. Attack numbers were invented; kept for comparison. |

Both share the same assets, the same window/audio stack, and the same tests.
Press **ESC** to quit.

```
run_pet_ai.bat          <- the good one
run_pet.bat             <- original, unchanged
```

`clauderesponse.py` is the pristine upstream copy that `calamitas_pet_ai.py`
was built from, byte-identical to what was supplied. Two small fixes were
applied on top; see "Changes made" below.

## Changes made to the ported AI

One, and it is a scope flag rather than a fix:

1. **`INCLUDE_POST_BROTHERS_STAGE` made explicit** (it was hardcoded as
   `_STAGE2_FROM = 8`). See the scope note below — this is the one flag you may
   want to flip.

No behaviour was retuned. Speeds, lifetimes, cadences, cooldowns and the
`phaseChange` table are untouched.

## Later change: real per-pixel alpha

Both versions now present via `UpdateLayeredWindow` + `ULW_ALPHA` instead of a
green colour key. The two "changes" the port used to carry — `blit()` accepting
3- and 4-channel canvases, and `_disc()` likewise — are both **obsolete**: with
the colour key gone there is no 3-channel canvas any more, `buf` is the full
4-channel premultiplied BGRA DIB, and `_disc()` writes an opaque alpha of 255.
Those workarounds have been deleted rather than left in place.

Visible effect: sprite edges are blended instead of hard-cut at `ALPHA_CUTOFF`,
and `KEY_BGR` / `ALPHA_CUTOFF` no longer exist in either module.

## Scope note you should decide on

`04_BlastPunchCast` and `06_Clap` are **only** drawn by the `LastStage`
(`ai[0]==3`) branch, which is post-brothers and outside the requested
pre-brothers scope. To make all 7 sprite animations reachable, the port routes
`phaseChange` table entries `>= 8` through the LastStage timings, so roughly
40% of her behaviour comes from that out-of-scope branch.

It is now a single flag near the top of `calamitas_pet_ai.py`:

```python
INCLUDE_POST_BROTHERS_STAGE = True   # False = strict pre-brothers
```

Set it to `False` and she is purely pre-brothers — but `04_BlastPunchCast` and
`06_Clap` then never play, and `test_headless.py` will fail its
"all 7 animations occur" assertion. That assertion encodes my earlier scoping
mistake, so if you flip the flag, relax that assertion too.

## Running the tests against either version

The suite takes a `CALAMITAS_MODULE` environment variable, defaulting to the
original pet:

```powershell
python test_headless.py                                  # original pet
$env:CALAMITAS_MODULE = "calamitas_pet_ai"; python test_headless.py   # ported AI
Remove-Item Env:\CALAMITAS_MODULE
```

All four tests currently pass for **both** implementations.

No install needed — it uses only the Python standard library plus `numpy` and
`Pillow`, which you already have. (`pygame` has no wheel for Python 3.14, so the
window is a raw Win32 layered window instead.)

Console output is quiet by default. Add `-v` for an fps/state line every 2
seconds, or `--shot <seconds>` to dump one composited frame to
`pet_debug_frame.png`.

```
python calamitas_pet.py            # normal
python calamitas_pet.py -v         # verbose
python calamitas_pet.py --shot 5   # also dump a debug frame after 5s
```

## Transparency: per-pixel alpha

The window is a layered window presented with `UpdateLayeredWindow` +
`ULW_ALPHA`, so the compositor blends each pixel's alpha. Sprite edges are
genuinely blended, and the soft outer glow on the projectiles survives intact.

Two things about this are load-bearing, and both were found the hard way:

**1. `pblend` must be a real `BLENDFUNCTION*`, not `NULL`.** MSDN says `pblend`
is ignored when `ULW_ALPHA` is set, so passing `NULL` is the natural thing to do
— and on this build (Windows 11 25H2, 26200) it fails *every* call with
`ERROR_GEN_FAILURE`. Supplying a `BLENDFUNCTION{AC_SRC_OVER, 0, 255,
AC_SRC_ALPHA}` makes it work. This one `NULL` was the entire reason the pet was
previously a colour-key window.

**2. Never call `SetLayeredWindowAttributes` on this window.** It switches the
window into colour-key / constant-alpha mode, and after that *every*
`UpdateLayeredWindow` call fails with `ERROR_INVALID_PARAMETER` — including
`flags=0`. The two transparency modes are mutually exclusive on one window.

The buffer is **premultiplied BGRA**, which is what `ULW_ALPHA` expects; the
colour channels are already scaled by alpha. PIL hands out straight
(non-premultiplied) alpha, so `blit()` does that multiply. Getting this wrong
is not subtle — sprite edges grow bright halos. `test_headless.py` has a
regression test for exactly that.

### How this was diagnosed

`ulw_probe.py` is the probe that established the above. It builds a layered
window, sweeps the documented call variants, and `BitBlt`s the **real desktop**
back to confirm the resulting pixels actually landed — checking the return code
alone is not enough, since an earlier per-pixel-alpha attempt reported success
at the API level while drawing nothing.

```
flags=0  (no ULW_ALPHA)          ok    OK
flags=ULW_ALPHA (0x02)           FAIL  ERROR_GEN_FAILURE
flags=0x01 / 0x04 / 0x20        ok    OK
colorkey then flags=0           FAIL  ERROR_INVALID_PARAMETER

interleaved A/B on pblend:
round   pblend=NULL              pblend=BLENDFUNCTION
0..3    FAIL / GEN_FAILURE       ok / OK          (4/4)
```

Ruled out along the way, so you don't have to re-check them: not an RDP session
(`SM_REMOTESESSION=0`), not a VM or basic display driver (real GPU, DWM
composition on), not a low-integrity sandbox, and not ctypes marshalling
(`0x01`/`0x04`/`0x20` all succeed through the same call). Alpha was then
verified as genuinely blending by compositing a premultiplied alpha ramp over
the desktop and reading the result back — max error 0.5/255.

## What it does

- Transparent, click-through, always-on-top window over your whole desktop, so
  she never blocks clicks on your other windows
- She hovers and bobs toward your cursor, switching to the taller hover pose
  when she moves fast
- Six attacks on independent cooldowns, all aimed at your cursor
- Her shield goes up during the dash
- Real sound effects
- She is free to drift off the edge of the screen and come back

## Scope (as requested)

### Forcefield

`calamitas_forcefield.py` re-implements `Effects/SupremeShieldShader.fx` as per-pixel
alpha, and the AI version draws it around her. It is a faithful port: the radial
profile matches the `.fx` to ~2.7/255, the shader's own truncated literals (`1.414`,
`3.141`) are preserved rather than "corrected", and `1.414f`/`0.088f`/`1.35f` are
applied per-branch because only the normal branch has the `3f`.

Assets (all downloaded from the mod repo, branch `1.4.4`):

| Asset | Path |
| --- | --- |
| Shader source | `assets/shaders/SupremeShieldShader.fx` |
| Base texture | `assets/shaders/ForcefieldTexture.png` |
| Branch textures | `assets/shaders/reference/{CentralGold,SemiCircularSmearVertical}.png` |
| Compiled shader | `assets/shaders/reference/SupremeShieldShader.xnb` (reference only) |

**When it is visible.** Faithfully, always. The original gates the sphere on
`forcefieldScale`, not `forcefieldOpacity`: it lerps to `0.45` while the shield
is up (`SupremeCalamitas.cs:612`) and back to `1` otherwise (`:619`), reaching `0`
only in the death phase. `forcefieldOpacity` stays at its `1f` default (`:171`)
for the whole normal fight — it is only lowered at BH4 (`:1304`) and post-music-hit
(`:1325`), both outside the pet's scope. So the sphere is always on, and it
**shrinks during a charge** rather than appearing and disappearing.

**Two mappings are invented**, because the shipped frames give us neither quantity:
the C# quad is 216px against a 152.8px hitbox, but the pet has no hitbox, so the
sphere is sized at `_FF_VS_SPRITE` (1.55) × her sprite height and follows `SCALE`
with it; and the sphere is centred on `self.x/self.y`, which is already her body
centre because `draw()` uses a centre anchor.

**Known limits.** The noise is a 1/f^1.5 FFT field, not Terraria's real Perlin, so
the cloud pattern inside the rim will not match. `_CHARGE_MIX` (0.5) and the
shimmer `intensity` mapping are invented. At `forcefieldScale = 0.45` the sphere is
smaller than her sprite and hides behind her — which is what the original does too
(62px bubble vs a 68.8px hitbox). Roughly 1.8 ms/frame at normal size.



| Attack | Animation | Projectile | Sound |
| --- | --- | --- | --- |
| Barrage | `03_BothArmsRaise` | `BrimstoneBarrage` ×3 | `BrimstoneShoot` |
| Fireblast | `02_Casting` | `SCalBrimstoneFireblast` | `BrimstoneShoot` |
| Wave | `05_LeftHandshake` | `BrimstoneWave` ×2 | `BrimstoneShoot` |
| Gigablast | `02_Casting` | `SCalBrimstoneGigablast` | `BrimstoneBigShoot` |
| Hellblast | `03_BothArmsRaise` | `BrimstoneHellblast` ×2 | `BrimstoneHellblastSound` |
| Shield bash | `04_BlastPunchCast` | — (dash + shield) | `SCalDash` |

`06_Clap` is used as an occasional idle flourish.

**Deliberately excluded:**

- The **Sepulcher** worm she summons
- **Cataclysm** and **Catastrophe**
- The whole **Permafrost phase** — `PermafrostMeat`,
  `PermafrostAbsoluteZeroProjectile`, `PermafrostBlaster`. Those are gated
  behind the `permafrost` flag / `ai[0] >= 3`, which only happens *after* the
  brothers are dead, so they are not part of the pre-brothers fight.
- `BrimstoneMonster` (the Whispering Maelstrom), which belongs to the same
  later phase. The sprite is present in `assets/sprites/` but unused.

## Where the assets came from

Official public mirror of the released mod, branch `1.4.4`:

    https://github.com/CalamityTeam/CalamityModPublic

| Asset | Source path |
| --- | --- |
| Body sprite | `NPCs/SupremeCalamitas/SupremeCalamitasHooded.png` |
| Shield | `NPCs/SupremeCalamitas/SupremeShield{Top,Bottom}.png` |
| Projectiles | `Projectiles/Boss/{BrimstoneBarrage,BrimstoneWave,BrimstoneHellblast,SCalBrimstoneFireblast,SCalBrimstoneGigablast}.png` |
| Sounds | `Sounds/Custom/SCalSounds/*.ogg` (converted to WAV with ffmpeg) |

The body uses the **Hooded** sheet, which is what the game swaps in for the
actual fight (per `PreDraw`: `DownedBossSystem.downedCalamitas ? NpcTexture : HoodedTexture`).

Body animation frames were sliced from that sheet using the cell layout already
derived in the parent folder — 2 columns × 21 rows, 6 frames per animation.

## Timings

Frame durations come from `FrameChangeSpeed` in `SupremeCalamitas.cs`, where
`frame_ms = 1000 / (FrameChangeSpeed * 60)`:

| Animation | Speed | Frame |
| --- | --- | --- |
| UpwardDraft | 0.15 | 111 ms |
| UpwardDraftTall | 0.15 | 111 ms |
| Casting | 0.15 | 111 ms |
| BothArmsRaise | 0.175 | 95 ms |
| BlastPunchCast | one-shot | 100 ms |
| LeftHandshake | 0.15 | 111 ms |
| Clap | 0.245 | 68 ms |

Projectile frame counts are `Main.projFrames[Type]` from each projectile's
source: Barrage 4, Wave 4, Hellblast 4, Fireblast 5, Gigablast 6.

## Layout

```
desktop_pet/
  calamitas_pet_ai.py  ported-AI pet  (run_pet_ai.bat)
  run_pet_ai.bat       launcher for the ported-AI version
  calamitas_pet.py     original pet   (run_pet.bat)
  run_pet.bat          launcher for the original
  clauderesponse.py    pristine upstream copy the port was built from
  calamitas_forcefield.py  SupremeShieldShader.fx ported to numpy (AI version only)
  ulw_probe.py         standalone UpdateLayeredWindow diagnostic (see above)
  test_headless.py     simulation tests (no window)
  test_present.py      per-frame presentation check
  test_on_screen.py    desktop read-back visibility check
  test_full_run.py     full run + on-screen captures
  assets/
    frames/SupremeCalamitasHooded/   7 animations x 6 frames
    sprites/                          projectiles + shield
    sfx/                              WAV conversions of her sounds
    shaders/                          forcefield shader + texture, and reference/
```

## Tests

```powershell
python test_headless.py     # 90s of simulation, no window
python test_present.py      # does every frame actually reach the screen?
python test_on_screen.py    # reads the desktop back; is she really visible?
python test_full_run.py     # full run with attacks, saves on-screen captures
```

`test_headless.py` checks the blitter (BGRA channel order, correct
premultiplication, partial alpha preserved rather than cut off, no bright halos
on dim sprites, offscreen no-ops), that every projectile's frames are uniformly
sized so nothing jitters, that all 7 animations and all 6 attacks occur, that
every sound fires, and that she both returns to the screen and leaves it.

`test_on_screen.py` and `test_full_run.py` `BitBlt` the **real desktop** back and
count her pixels. This matters: during development the per-pixel-alpha version
reported success at the API level while drawing nothing at all, and a screenshot
looked like an empty desktop. These tests read the composited screen, so they
cannot be fooled. `test_full_run.py` writes `onscreen_<n>.png` captures.

A screenshot of the pet is a legitimate way to confirm it works: the
per-pixel-alpha window composites into a normal screen capture, with the desktop
showing through behind her.

`test_on_screen.py` no longer filters by key colour — there isn't one. It grabs
the bare desktop first as a baseline, then diffs against it, so "something we
drew is actually there" is measured rather than assumed.

## Notes

- The window is click-through twice over: `WS_EX_TRANSPARENT` plus
  `WM_NCHITTEST -> HTTRANSPARENT`, so it never steals clicks or focus.
- A window must be made visible explicitly — `WS_POPUP` does **not** include
  `WS_VISIBLE`, and an invisible window shows nothing at all.
- `UpdateLayeredWindow` wants a DC compatible with the **screen**, not with the
  window, so `present()` passes `GetDC(None)` rather than `GetDC(hwnd)`.
- `SCALE = 4` near the top controls her size; `FPS = 60` the frame cap.
- Sprites belong to Azafure LLC / Re-Logic. Personal, non-commercial use only;
  the mod is not affiliated with Terraria or Re-Logic.