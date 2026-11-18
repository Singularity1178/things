"""On-demand model loading: Character(lazy=True), clips that bake on first use, unload / reload.

    python test_lazy_models.py

No window and no GPU: it only reads the .glb files in model/ (Character.ctx stays None, so no
renderer is ever built). See PERFORMANCE.md for what this protects.
"""
from __future__ import annotations

import contextlib
import io
import math
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np                                    # noqa: E402
from PySide6 import QtWidgets                         # noqa: E402

_qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

from bapet.character import Character                 # noqa: E402
from bapet.config import BAKE_FPS, MODEL_DIR          # noqa: E402
from bapet.gltf import sample_channel                 # noqa: E402
from bapet.profiles import load_profile               # noqa: E402

FAILS = []


def check(ok, msg):
    if not ok: FAILS.append(msg)
    print(("  ok    " if ok else "  FAIL  ") + msg)


def quiet(fn, *a, **k):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **k)


def reference_bake(model, anim):
    """The old load-everything loop for ONE animation: channel by channel, no sharing."""
    g, T, Q, S = model.g, model.rest_T, model.rest_Q, model.rest_S
    chans, dur = [], 0.0
    for ch in anim["channels"]:
        tg = ch["target"]
        if "node" not in tg or tg["path"] not in ("translation", "rotation", "scale", "weights"): continue
        sm = anim["samplers"][ch["sampler"]]
        times = g.accessor(sm["input"]).astype(np.float64).reshape(-1)
        vals = g.accessor(sm["output"]).astype(np.float64)
        chans.append((tg["node"], tg["path"], times, vals, sm.get("interpolation", "LINEAR")))
        dur = max(dur, float(times[-1]))
    dur = max(dur, 1 / 30)
    F = max(2, int(math.ceil(dur * BAKE_FPS)) + 1)
    ts = np.linspace(0, dur, F)
    cT = np.repeat(T[None], F, 0); cQ = np.repeat(Q[None], F, 0); cS = np.repeat(S[None], F, 0)
    for node, path, times, vals, interp in chans:
        if path == "weights": continue
        v = sample_channel(times, vals, interp, ts, path)
        if path == "translation": cT[:, node] = v
        elif path == "rotation": cQ[:, node] = v
        else: cS[:, node] = v
    return dur, cT, cQ, cS


def main():
    paths = sorted(MODEL_DIR.glob("*.glb"), key=lambda q: q.stat().st_size)
    if not paths:
        print("no .glb in model/ - nothing to test"); return 0
    path = paths[0]                                   # the smallest model keeps this quick
    print(f"model: {path.name}")

    # [1] a lazy character is a name and some stats: the file is not opened
    t0 = time.perf_counter()
    roster = [Character(p, i, profile=load_profile(p), lazy=True) for i, p in enumerate(paths)]
    dt = time.perf_counter() - t0
    ch = roster[0]
    check(not any(c.loaded for c in roster) and "model" not in ch.__dict__,
          f"[1] {len(roster)} lazy characters listed in {dt * 1000:.0f} ms, none of them read")
    check(bool(ch.name) and ch.firing_range > 0 and ch.ammo_per_attack >= 1,
          "[1] name and combat stats are there without the file")
    check(ch.renderer is None and not ch.loaded, "[1] no GL context -> renderer is None, and asking did not load her")

    # [2] the first touch of anything heavy reads the file
    fit = quiet(lambda: ch.fit)
    m = ch.model
    baked = [n for n, c in m.clips.items() if c.baked]
    check(ch.loaded and fit["H"] > 0 and "idle" in ch.clips, "[2] reading ch.fit loaded her (clips, window, fit)")
    check(len(baked) < len(m.clips) or len(m.clips) <= 2,
          f"[2] {len(baked)} of {len(m.clips)} clips baked by loading (the rest wait until they are played)")

    # [3] a clip baked on demand is the clip the old loop made
    anims = m.g.j.get("animations", [])
    worst = 0.0; same = True
    for ai, a in enumerate(anims[:6]):
        c = m.clips[a.get("name", f"anim{ai}")]
        dur, cT, cQ, cS = reference_bake(m, a)
        same &= (abs(dur - c.duration) < 1e-12 and np.array_equal(cT, c.T) and np.array_equal(cQ, c.Q)
                 and np.array_equal(cS, c.S))
        worst = max(worst, float(np.abs(cQ - c.Q).max()) if cQ.shape == c.Q.shape else 9.0)
    check(same, f"[3] on-demand bake == channel-by-channel bake for {min(6, len(anims))} clips (max diff {worst:.1e})")
    c0 = next(iter(m.clips.values()))
    check(c0.pin.shape == (c0.T.shape[0], 3) and c0.origin.shape == (3,) and c0.ssig is not None,
          "[3] pin / origin / scale signature are filled in with the bake")

    # [4] unload forgets the model, the next touch brings the same one back
    gen = ch.generation
    ch.window_scale = 9.0                             # what a ride window would have left behind
    ch.unload()
    check(not ch.loaded and ch.generation == gen + 1 and "model" not in ch.__dict__ and "clips" not in ch.__dict__
          and ch.window_scale == ch.profile["window_scale"], "[4] unload() drops the model and resets her window")
    fit2 = quiet(lambda: ch.fit)
    check(ch.loaded and ch.model is not m and fit2 == fit, "[4] touching her again reloads the same fit")

    # [5] pins and has_ride
    ch.pin(); ch.pin(); ch.unpin()
    check(ch.pins == 1, "[5] pin / unpin count")
    ch.unpin()
    lazy2 = roster[-1]
    r = lazy2.has_ride
    check(not lazy2.loaded and isinstance(r, bool), f"[5] has_ride answered from the profile without loading ({lazy2.path.name}: {r})")

    # [6] a file that cannot be read fails once, loudly, and then fast
    bad = Character(Path("no_such_model.glb"), 0, lazy=True)
    try: quiet(bad.load); raised = False
    except Exception: raised = True
    t0 = time.perf_counter()
    try: bad.load(); again = False
    except RuntimeError: again = True
    check(raised and again and bad.failed and not bad.loaded and time.perf_counter() - t0 < 0.05,
          "[6] unreadable file: load() raises, remembers it, and does not retry the read")

    # [7] Character(path) on its own still loads at once (tools and older tests rely on it)
    eager = quiet(Character, path, 0, False, load_profile(path))
    check(eager.loaded and eager.fit == fit, "[7] Character(path) without lazy=True is loaded straight away")

    print("PASS" if not FAILS else f"{len(FAILS)} check(s) FAILED")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
