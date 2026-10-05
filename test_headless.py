"""Headless test of the pet's simulation: no window, no audio.

Checks the asset loaders, the blitter, the state machine and projectile
steering. She is allowed to leave the canvas, so the "is something visible"
assertion is a ratio over the run rather than a per-frame guarantee.
"""

import math
import os
import random
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# CALAMITAS_MODULE selects which implementation to test (default: original pet)
_MOD = os.environ.get("CALAMITAS_MODULE", "calamitas_pet")
cp = __import__(_MOD)


class SilentAudio:
    def __init__(self):
        self.played = []

    def play(self, name, volume=None):
        self.played.append(name)


def is_blank(c):
    """True where the canvas is fully transparent (alpha 0). Works on a single
    pixel or an array."""
    return c[..., 3] == 0


def blank(h, w):
    """A fully transparent premultiplied BGRA canvas."""
    return np.zeros((h, w, 4), dtype=np.uint8)


def test_blit():
    """Alpha blend into a premultiplied BGRA canvas: BGRA order, correct
    premultiplication, and partial alpha preserved rather than cut off."""
    canvas = blank(80, 80)
    sprite = np.zeros((20, 20, 4), dtype=np.uint8)
    sprite[:, :, :3] = (255, 0, 0)     # red in RGBA
    sprite[:, :, 3] = 255              # fully opaque
    cp.blit(canvas, sprite, 30, 25)

    assert not bool(is_blank(canvas[35, 40])), "blit produced nothing at the requested offset"
    assert bool(is_blank(canvas[0, 0])), "blit painted outside the sprite"
    # canvas is BGRA, sprite RGBA -> red must land in the R slot (index 2)
    assert canvas[35, 40, 2] == 255, f"red not in R slot: {canvas[35, 40]}"
    assert canvas[35, 40, 0] == 0, f"red leaked into B slot: {canvas[35, 40]}"
    assert canvas[35, 40, 3] == 255, f"alpha not preserved: {canvas[35, 40]}"

    # A fully transparent source pixel must stay untouched, so layered sprites
    # composite over each other instead of erasing them.
    canvas[:] = 0
    canvas[:, :, 0] = 200              # sentinel in the B slot
    sprite[:, :, 3] = 0
    cp.blit(canvas, sprite, 0, 0)
    assert bool(is_blank(canvas[10, 10])), "transparent source pixel wrote alpha"
    assert canvas[10, 10, 0] == 200, "transparent source pixel overwrote the canvas"

    # Half alpha: premultiplied means red is scaled to 128, NOT left at 255.
    # Leaving it at 255 is the classic bug -- it produces bright halos.
    canvas[:] = 0
    sprite[:, :, 3] = 128
    cp.blit(canvas, sprite, 0, 0)
    px = canvas[10, 10]
    assert px[3] == 128, f"alpha wrong: {px}"
    assert 126 <= px[2] <= 129, f"red not premultiplied (got {px[2]}, want ~128): {px}"
    print("blit: BGRA order / premultiply / alpha preserved / bounds OK")


def test_blit_darkens_no_halo():
    """A dim source must not contribute more colour than its alpha allows."""
    canvas = blank(16, 16)
    sprite = np.zeros((16, 16, 4), dtype=np.uint8)
    sprite[:, :, :3] = (255, 255, 255)
    sprite[:, :, 3] = 32
    cp.blit(canvas, sprite, 0, 0)
    for ch in range(3):
        assert canvas[8, 8, ch] <= 32, \
            f"channel {ch} = {canvas[8, 8, ch]} exceeds alpha 32 (not premultiplied?)"
    print("blit: dim sprites stay dim (no halo): OK")


def test_out_of_bounds_is_safe():
    """Fully-offscreen draws are no-ops; partially-onscreen draws paint the overlap."""
    canvas = blank(60, 60)
    sprite = np.zeros((30, 30, 4), dtype=np.uint8)
    sprite[:, :, :3] = (255, 255, 255)
    sprite[:, :, 3] = 255
    for pos in [(-100, -100), (0, -50), (-50, 0), (10_000, 10_000), (60, 0), (0, 60)]:
        cp.blit(canvas, sprite, *pos)
    assert is_blank(canvas).all(), "fully-offscreen blit leaked pixels"

    # straddling the right/bottom edge must still paint the visible corner
    cp.blit(canvas, sprite, 55, 55)
    assert not bool(is_blank(canvas[59, 59])), "edge-straddling blit did not paint the overlap"
    assert bool(is_blank(canvas[40, 40])), "edge-straddling blit painted outside the sprite"
    print("blit: offscreen no-ops + edge-straddling overlap OK")


def main():
    random.seed(7)
    np.random.seed(7)
    W, H = 640, 360
    canvas = blank(H, W)

    test_blit()
    test_blit_darkens_no_halo()
    test_out_of_bounds_is_safe()

    audio = SilentAudio()
    cal = cp.Calamitas(W, H, audio)
    print("anims loaded:", {k: len(v.frames) for k, v in cal.anims.items()})
    print("projectile frame counts:", {k: len(v) for k, v in cal.proj.items()})

    for name, frames in cal.proj.items():
        sizes = {f.shape[:2] for f in frames}
        assert len(sizes) == 1, f"{name} frames differ in size: {sizes}"
    print("projectile frames uniformly sized (no jitter): OK")

    seen_states, seen_attacks = set(), set()
    attack_counts = {}
    max_bullets = 0
    visible = 0
    drawn = 0
    xs, ys = [], []
    cursor = (W * 0.8, H * 0.3)
    was_attacking = False

    for step in range(60 * 90):
        cursor = (W * 0.5 + math.cos(step * 0.01) * W * 0.75,
                  H * 0.5 + math.sin(step * 0.017) * H * 0.75)
        cal.update(1 / 60.0, cursor)
        seen_states.add(cal.current)
        attacking = cal.attack is not None
        if attacking and not was_attacking and cal.attack:
            k = cal.attack["key"]
            seen_attacks.add(k)
            attack_counts[k] = attack_counts.get(k, 0) + 1
        was_attacking = attacking
        xs.append(cal.x)
        ys.append(cal.y)
        max_bullets = max(max_bullets, len(cal.bullets))

        if step % 3 == 0:
            canvas[:] = 0
            cal.draw(canvas)
            drawn += 1
            if cp.painted_pixels(canvas) > 0:
                visible += 1

    print("states seen:", sorted(seen_states))
    print("attacks fired:", sorted(seen_attacks))
    print("attack counts:", attack_counts)
    print("max simultaneous projectiles:", max_bullets)
    print("sfx triggered:", sorted(set(audio.played)))
    print(f"position x range: {min(xs):.0f} .. {max(xs):.0f}   "
          f"y range: {min(ys):.0f} .. {max(ys):.0f}")
    print(f"on-canvas frames: {visible}/{drawn} ({visible / drawn:.0%})")

    all_keys = {"barrage", "fireblast", "wave", "gigablast", "hellblast", "bash"}
    missing = all_keys - seen_attacks
    assert not missing, f"these attacks never fired: {sorted(missing)}"
    assert len(seen_states) == 7, f"only used {len(seen_states)}/7 animations: {sorted(seen_states)}"
    assert max_bullets > 0, "no projectiles were ever spawned"
    assert visible / drawn > 0.5, "she was off-canvas most of the time (stuck?)"
    went_outside = min(xs) < 0 or max(xs) > W or min(ys) < 0 or max(ys) > H
    print(f"left the canvas during the run: {went_outside}")
    print("\nall headless checks passed")


if __name__ == "__main__":
    main()
