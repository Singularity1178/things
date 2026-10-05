#!/usr/bin/env python3
"""calamitas_forcefield.py -- Supreme Calamitas' forcefield sphere as pure-numpy, per-pixel-alpha pixels.

PROVENANCE NOTES (the file carries its own write-up)
* Only SupremeShieldShader.fx was attached. Its line numbers (`.fx:N`) are exact. Every `SupremeCalamitas.cs`
  figure (DrawForcefield :3669, hitboxSize :565) comes from the brief's summary of the C# and has NOT been
  checked against the C# itself.
* The shader's literals are ported as written: 1.414 (not 1.4142) and 3.141 (not pi).
* The shader never reads `sampleColor` (COLOR0) or `uRotation`. So the C# tint `Color.White * opacity` has no effect
  in-game, and rotation only turns the quad (the noise and the base texture), never the radial profile.
  `forcefieldOpacity` is still applied here as a global alpha multiplier because the overlay needs a fade (invented).
* uSaturation = 1 makes `min(1, pow(sin(t*3.1),10)*0.7 + uSaturation)` a constant 1, so the pulse is dead in the
  original (the gain is a constant 2.45). It is kept general: state['intensity'] is uSaturation, and the pulse
  only shows when intensity < 1.
* Textures (ForcefieldTexture / CentralGold / SemiCircularSmearVertical) are optional. They are loaded from
  assets/sprites if present, otherwise a flat stand-in colour is used (invented). Perlin noise is generated here.
* HOW THE `start` BREATHING IS HANDLED: everything in the shader except the noise is a function of the radius only.
  So a 1-D radial LUT (2048 entries) is rebuilt every frame (microseconds), and the per-pixel work is two gathers
  from it plus the noise. The 2-D parts (radius->LUT index, colour, noise coordinates) depend only on
  (quad size, branch, charge) and are cached.
"""
import math
from pathlib import Path
from time import perf_counter

import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
# The forcefield textures live in assets/shaders/, not assets/sprites/: the normal branch's
# ForcefieldTexture.png sits beside the shader, and the two branch textures are reference-only
# under assets/shaders/reference/. _TEXTURE_DIRS is searched in order and the first hit wins,
# so each texture resolves whether or not the reference/ set is present.
_SPR = HERE / "assets" / "sprites"
_SHADER_DIR = HERE / "assets" / "shaders"
_REF_DIR = _SHADER_DIR / "reference"
TEXTURE_DIRS = (_SHADER_DIR, _REF_DIR, _SPR)
USE_TEXTURES = True          # invented -- set False to always use the flat stand-in base colour

# ----------------------------------------------------------------------------------------------
# Source constants (private)
# ----------------------------------------------------------------------------------------------
# Texture sizes, from the brief (the PNGs are not attached)
_TEX_PX = {"forcefield": 72, "gold": 2048, "smear": 156}
_TEX_FILES = {"forcefield": "ForcefieldTexture.png", "gold": "CentralGold.png",
              "smear": "SemiCircularSmearVertical.png"}
# Each branch draws its own texture at its own scale. The C# draw calls are per-branch and only the
# normal one has the 3f, so the multipliers must not be shared. Quads at forcefieldScale=1,
# pureVisualScale=1, k=1:
#   SupremeCalamitas.cs:3743 ForcefieldTexture.png         72 px * 3.0   = 216 px
#   SupremeCalamitas.cs:3741 CentralGold.png            2048 px * 0.088 = 180 px
#   SupremeCalamitas.cs:3745 SemiCircularSmearVertical  156 px * 1.35  = 211 px
_QUAD_MUL = {"forcefield": 3.0, "gold": 0.088, "smear": 1.35}
_SMEAR_ALPHA = 0.3           # SupremeCalamitas.cs:3669 invincible branch `alpha * 0.3`
_U_OPACITY = 0.65            # SupremeCalamitas.cs:3669 uOpacity = 0.65f
_U_SATURATION = 1.0          # SupremeCalamitas.cs:3669 uSaturation = 1 (default for state['intensity'])
_DARK_VIOLET = (148 / 255.0, 0.0, 211 / 255.0)   # SupremeCalamitas.cs:3669 uColor = Color.DarkViolet (148,0,211)
_MAGENTA = (1.0, 0.0, 1.0)                       # SupremeCalamitas.cs:3669 uColor = Color.Magenta when charging
_RED_X14 = (1.0, 0.0, 0.0)   # SupremeCalamitas.cs:3669 uSecondaryColor = Color.Red * 1.4f (255*1.4 clamps to 255)
_CHARGE_MIX = 0.5            # invented -- brief says "lerped toward magenta when charging" with no amount

# SupremeShieldShader.fx constants
_NOISE_SCROLL = 0.7          # .fx:24 frac(coords.y + uTime * 0.7)
_START_BASE = 0.45           # .fx:25 start = 0.45 + sin(uTime * 3) * 0.04
_START_AMP = 0.04            # .fx:25
_START_RATE = 3.0            # .fx:25
_RING_R = 0.1                # .fx:26 ringRadus
_DIST_K = 1.414              # .fx:27 distance(coords, 0.5) * 1.414
_COLOR_EXP = 0.6             # .fx:28 pow(distanceRatio, 0.6)
_ALPHA_EXP = 2.6             # .fx:29 pow(inverseLerp(distanceRatio, 0, start), 2.6)
_PI_HLSL = 3.141             # .fx:30 sin(3.141 * ...) -- the shader's own truncated pi
_RING_AMP = 1.96             # .fx:30
_NOISE_GAIN = 2.6            # .fx:32 lerp(1, 2.6, noiseColor.r)
_EDGE_FRAC = 0.5             # .fx:34 inverseLerp(distanceRatio, start, start + ringRadus * 0.5)
_FALL_END = 0.56             # .fx:36 lerp(1, 0.56, distanceRatio)
_PULSE_GAIN = 2.45           # .fx:36 lerp(1, 2.45, ...)
_PULSE_RATE = 3.1            # .fx:36 sin(uTime * 3.1)
_PULSE_POW = 10              # .fx:36 pow(..., 10)
_PULSE_AMP = 0.7             # .fx:36 * 0.7
_BASE_MIX = 0.5              # .fx:37 lerp(baseColor.rgb, colorToUse, 0.5)

# Perlin stand-in (the real Images/Misc/Perlin is vanilla Terraria and not available). All invented.
_NOISE_N = 256               # invented -- tile size
_NOISE_BETA = 1.5            # invented -- amplitude ~ 1/f^1.5, cloud-like
_NOISE_SPREAD = 0.2          # invented -- stddev of the field around 0.5 before clipping to [0, 1]
_NOISE_SEED = 1337           # invented -- fixed so the pattern is identical every run

# Rendering / cache tuning (all invented -- presentation and performance only)
_LUT_N = 2048                # radial LUT entries over [0, _DR_MAX]
_QUAD_STEP = 2               # quad size is rounded to a multiple of this, so a smoothly animated scale hits the cache
_MIN_QUAD = 16               # smaller quads are not drawn
_MAX_QUAD = 1200             # safety cap against runaway scale
_RAD_CACHE = 6               # LRU sizes
_TINT_CACHE = 6
_BASE_MAX = 256              # textures are downsampled to this before use
_PREMULTIPLY_TEX = True      # invented -- tModLoader premultiplies on load, so the shader sees rgb*alpha
# Flat base colour (BGR) when a PNG is missing. 132/255 = the forcefield PNG's peak alpha (premultiplied white).
_STANDIN = {"forcefield": (132 / 255.0,) * 3, "gold": (0.2, 0.82, 1.0), "smear": (1.0, 1.0, 1.0)}

# Alpha beyond start + ringRadus is sin(3.141)*1.96 = 0.0012 -> < 0.5/255 after all gains, so the sphere is
# bounded by the largest start + ring: 0.45 + 0.04 + 0.1.
_DR_MAX = _START_BASE + _START_AMP + _RING_R

_BILINEAR = getattr(getattr(Image, "Resampling", Image), "BILINEAR")
_BOX = getattr(getattr(Image, "Resampling", Image), "BOX")


# ----------------------------------------------------------------------------------------------
# Module-level caches (built once at import)
# ----------------------------------------------------------------------------------------------
def _make_noise(n=_NOISE_N, seed=_NOISE_SEED):
    """Seamless greyscale field in [0, 1]: white noise shaped by a 1/f^beta spectrum. FFT output is periodic on
    both axes, so the frac() wrap in the scroll never shows a seam. Runs once; float32 afterwards."""
    rng = np.random.default_rng(seed)
    white = rng.standard_normal((n, n)).astype(np.float32)
    f = np.fft.fftfreq(n).astype(np.float32)
    fr = np.sqrt(f[None, :] ** 2 + f[:, None] ** 2)
    fr[0, 0] = 1.0
    spec = np.fft.fft2(white) * (fr ** -_NOISE_BETA)
    spec[0, 0] = 0.0
    field = np.fft.ifft2(spec).real.astype(np.float32)
    z = (field - field.mean()) / field.std()
    return np.ascontiguousarray(np.clip(0.5 + _NOISE_SPREAD * z, 0.0, 1.0).astype(np.float32))


_NOISE = _make_noise()
_LUT_R = np.linspace(0.0, _DR_MAX, _LUT_N, dtype=np.float32)                 # distanceRatio per LUT slot
_LUT_FALL = (1.0 + (_FALL_END - 1.0) * _LUT_R).astype(np.float32)            # .fx:36 lerp(1, 0.56, distanceRatio)
_CLUT = {}                                                                   # charge -> (N+1, 3) BGR colorToUse
_BASE = {}                                                                   # branch -> PIL RGB tile or None


def _color_lut(charge):
    """colorToUse (.fx:28) per LUT slot, BGR. It depends on distanceRatio only, never on `start`: cached per charge."""
    lut = _CLUT.get(charge)
    if lut is None:
        u = np.asarray(_MAGENTA if charge else _DARK_VIOLET, np.float32)
        sec = np.asarray(_RED_X14, np.float32)
        if charge:
            sec = sec + (np.asarray(_MAGENTA, np.float32) - sec) * np.float32(_CHARGE_MIX)
        t = (_LUT_R ** _COLOR_EXP)[:, None]
        rgb = u + (sec - u) * t                                              # lerp(uColor, uSecondaryColor, t)
        lut = np.empty((_LUT_N + 1, 3), np.float32)
        lut[:_LUT_N] = rgb[:, ::-1]
        lut[_LUT_N] = lut[_LUT_N - 1]                                        # the "outside" slot is never visible
        _CLUT[charge] = lut
    return lut


def _find_texture(name):
    """First existing copy of `name` across TEXTURE_DIRS, or None."""
    for d in TEXTURE_DIRS:
        p = d / name
        if p.exists():
            return p
    return None


def _load_base(branch):
    """Optional PNG -> RGB PIL tile (premultiplied, <= _BASE_MAX px), or None (flat stand-in)."""
    if branch in _BASE:
        return _BASE[branch]
    tile = None
    if USE_TEXTURES:
        p = _find_texture(_TEX_FILES[branch])
        try:
            if p is not None:
                im = Image.open(p).convert("RGBA")
                if max(im.size) > _BASE_MAX:
                    im = im.resize((_BASE_MAX, _BASE_MAX), _BOX)
                arr = np.asarray(im, dtype=np.float32)
                if _PREMULTIPLY_TEX:
                    arr[:, :, :3] *= arr[:, :, 3:4] / 255.0
                tile = Image.fromarray(np.clip(arr[:, :, :3] + 0.5, 0, 255).astype(np.uint8), "RGB")
        except Exception:
            tile = None
    _BASE[branch] = tile
    return tile


def _cached(cache, key, build, cap):
    hit = cache.pop(key, None)
    if hit is None:
        hit = build()
        while len(cache) >= cap:
            cache.pop(next(iter(cache)))
    cache[key] = hit
    return hit


# ----------------------------------------------------------------------------------------------
# Cached geometry
# ----------------------------------------------------------------------------------------------
class _Radial:
    """Static per quad size: pixel -> LUT slot, plus noise sampling coordinates. Covers only the sphere's box."""
    __slots__ = ("q", "hs", "side", "dx", "dy", "idx", "ucol", "vrow")

    def __init__(self, q):
        self.q = q
        hs = int(math.ceil(_DR_MAX / _DIST_K * q))      # half-extent in px: dr = d/q * 1.414 <= _DR_MAX
        self.hs, self.side = hs, 2 * hs + 1
        off = np.arange(-hs, hs + 1, dtype=np.float32)  # pixel centre offsets from the quad centre
        self.dx = self.dy = off
        d = np.sqrt(off[None, :] ** 2 + off[:, None] ** 2)
        fi = d * np.float32(_DIST_K / q * (_LUT_N - 1) / _DR_MAX)
        idx = np.rint(fi).astype(np.intp)
        idx[fi > _LUT_N - 1] = _LUT_N                   # outside the sphere -> the zero slot
        self.idx = idx
        # noise coords in the unrotated quad: u = 0.5 + dx/q, v = 0.5 + dy/q (tex2D coords, .fx:24)
        self.ucol = np.clip(np.floor((off / q + 0.5) * _NOISE_N), 0, _NOISE_N - 1).astype(np.intp)
        self.vrow = ((off / q) + 0.5) * np.float32(_NOISE_N)


class _Tint:
    """Static per (branch, quad size, charge): premultiplier colour = lerp(base, colorToUse, 0.5) (.fx:37), BGR."""
    __slots__ = ("rgb", "half", "base_q")

    def __init__(self, rad, branch, charge):
        half = _color_lut(charge).take(rad.idx, axis=0)
        half *= np.float32(1.0 - _BASE_MIX)
        tile = _load_base(branch)
        if tile is None:
            self.base_q = None
            rgb = half + np.float32(_BASE_MIX) * np.asarray(_STANDIN[branch], np.float32)
        else:
            q, hs = rad.q, rad.hs
            bq = np.asarray(tile.resize((q, q), _BILINEAR), np.float32) * np.float32(1.0 / 255.0)
            self.base_q = np.ascontiguousarray(bq[:, :, ::-1])      # RGB -> BGR, full quad (for rotated sampling)
            c = q // 2                                              # q is even (_QUAD_STEP)
            rgb = half + np.float32(_BASE_MIX) * self.base_q[c - hs:c + hs + 1, c - hs:c + hs + 1]
        self.rgb, self.half = np.ascontiguousarray(rgb), half


# ----------------------------------------------------------------------------------------------
# Forcefield
# ----------------------------------------------------------------------------------------------
class Forcefield:
    """One forcefield sphere. update() takes the state dict and refreshes the 1-D radial profile; draw() composites
    the sphere's bounding box into a premultiplied BGRA canvas. Centre = `forcefield` arg of draw() (see there)."""

    def __init__(self, size=(1920, 1080), scale=1.0, time=0.0):
        self.w, self.h = int(size[0]), int(size[1])
        self.scale = float(scale)           # world scale, like Calamitas.k (1.0 -> 216 px sphere)
        self._t0 = float(time)
        self.center = (self.w / 2.0, self.h / 2.0)
        self._rad = {}
        self._tint = {}
        self._L = np.zeros(_LUT_N + 1, np.float32)    # per-frame: alpha profile * all scalar gains * 255
        self._NM = np.zeros(_LUT_N + 1, np.float32)   # per-frame: noise weight (1.6 inside `start`, 0 outside)
        self.reset()

    def reset(self):
        """Back to construction state. The geometry caches are pure functions of their keys, so they are kept."""
        self.time = self._t0
        self.forcefield_scale = 1.0         # invented defaults for missing keys
        self.opacity = 1.0
        self.pure_scale = 1.0
        self.intensity = _U_SATURATION
        self.will_charge = False
        self.post_music_hit = False
        self.invincible = False
        self.rotation = 0.0
        self._refresh()

    def update(self, dt, state):
        """Advance uTime by dt seconds and apply `state`. Missing keys keep their previous value. Optional extra
        key 'center' = (x, y) sets the default sphere centre."""
        self.time += max(0.0, float(dt))
        s = state or {}
        if "forcefieldScale" in s:
            self.forcefield_scale = max(0.0, float(s["forcefieldScale"]))
        if "forcefieldOpacity" in s:
            self.opacity = min(1.0, max(0.0, float(s["forcefieldOpacity"])))
        if "forcefieldPureVisualScale" in s:
            self.pure_scale = max(0.0, float(s["forcefieldPureVisualScale"]))
        if "intensity" in s:
            self.intensity = max(0.0, float(s["intensity"]))
        if "willCharge" in s:
            self.will_charge = bool(s["willCharge"])
        if "postMusicHit" in s:
            self.post_music_hit = bool(s["postMusicHit"])
        if "invincible" in s:
            self.invincible = bool(s["invincible"])
        if "rotation" in s:
            self.rotation = float(s["rotation"])
        if s.get("center") is not None:
            self.center = (float(s["center"][0]), float(s["center"][1]))
        self._refresh()

    # ------------------------------------------------------------------ per-frame scalar work
    def _refresh(self):
        # SupremeCalamitas.cs:3669 three mutually exclusive branches: postMusicHit, then not-invincible, then invincible
        if self.post_music_hit:
            self.branch = "gold"
        elif not self.invincible:
            self.branch = "forcefield"
        else:
            self.branch = "smear"
        q = (_TEX_PX[self.branch] * _QUAD_MUL[self.branch] * self.forcefield_scale
             * self.pure_scale * self.scale)
        q = min(max(q, 0.0), float(_MAX_QUAD))
        self.quad = int(round(q / _QUAD_STEP)) * _QUAD_STEP
        # SupremeCalamitas.cs:3669 rotation = rotateToPlayer, only while postMusicHit
        self.rot = self.rotation if self.post_music_hit else 0.0
        self._profile()

    def _profile(self):
        """Rebuild the 1-D radial profile for the current uTime. This is the whole answer to the `start` breathing:
        everything except the noise depends on distanceRatio alone, so the 2-D arrays are untouched."""
        t, r = self.time, _LUT_R
        start = _START_BASE + math.sin(t * _START_RATE) * _START_AMP                 # .fx:25
        a_in = np.minimum(r * (1.0 / start), 1.0) ** _ALPHA_EXP                      # .fx:29 (r >= 0, saturate low is a no-op)
        ring = np.clip((r - start) * (1.0 / _RING_R), 0.0, 1.0)                      # .fx:30 inverseLerp(dr, start, start+ring)
        ring_a = np.sin(_PI_HLSL * ring) * _RING_AMP                                 # .fx:30
        edge = np.clip((r - start) * (1.0 / (_RING_R * _EDGE_FRAC)), 0.0, 1.0)       # .fx:34
        inside = r < start                                                           # .fx:31
        a = np.where(inside, a_in, a_in + (ring_a - a_in) * edge)                    # .fx:32 (noise applied per pixel) / .fx:34
        a *= _LUT_FALL                                                               # .fx:36 lerp(1, 0.56, dr)
        sat = self.intensity                                                         # uSaturation
        pulse = math.sin(t * _PULSE_RATE) ** _PULSE_POW * _PULSE_AMP + sat           # .fx:36
        pulse = 1.0 + (_PULSE_GAIN - 1.0) * min(1.0, pulse)                          # .fx:36 lerp(1, 2.45, min(1, ...))
        k = pulse * _U_OPACITY * self.opacity * 255.0                                # .fx:38 uOpacity; 255 folds in the 8-bit scale
        if self.branch == "smear":
            k *= _SMEAR_ALPHA
        np.multiply(a, np.float32(k), out=self._L[:_LUT_N])
        np.multiply(inside, np.float32(_NOISE_GAIN - 1.0), out=self._NM[:_LUT_N])    # lerp(1, 2.6, n) = 1 + 1.6 n
        # slot _LUT_N stays 0: pixels outside the sphere

    # ------------------------------------------------------------------ noise
    def _sample(self, rad, sy, sx, rot):
        """Scrolling noise (.fx:24) over the box slice. Unrotated: two 1-D index vectors. Rotated: per-pixel (u, v)
        in the turned quad, also returned so the base texture can use them."""
        n = _NOISE_N
        shift = ((self.time * _NOISE_SCROLL) % 1.0) * n          # frac(v + uTime*0.7), in noise rows
        if abs(rot) < 1e-6:
            rows = np.floor(rad.vrow[sy] + shift).astype(np.intp) % n
            return _NOISE[rows[:, None], rad.ucol[sx][None, :]], None, None
        c, s = math.cos(rot), math.sin(rot)                      # inverse of a clockwise (screen-space) rotation
        k = 1.0 / rad.q
        dxs, dys = rad.dx[sx][None, :], rad.dy[sy][:, None]
        u = (dxs * c + dys * s) * k + 0.5
        v = (dys * c - dxs * s) * k + 0.5
        cols = np.clip(np.floor(u * n), 0, n - 1).astype(np.intp)
        rows = np.floor(v * n + shift).astype(np.intp) % n
        return _NOISE[rows, cols], u, v

    # ------------------------------------------------------------------ drawing
    def _resolve_center(self, where):
        if where is None:
            return self.center
        if isinstance(where, dict):
            c = where.get("center")
            c = c if c is not None else (where["x"], where["y"])
        elif hasattr(where, "x") and hasattr(where, "y"):
            c = (where.x, where.y)
        else:
            c = where
        return float(c[0]), float(c[1])

    def draw(self, canvas, forcefield=None):
        """Composite into `canvas` (premultiplied BGRA uint8). `forcefield` = where to draw: an (x, y) pair, a dict
        with 'center' or 'x'/'y', or any object with .x/.y (e.g. Calamitas); None -> the stored centre.
        Returns the number of pixels written."""
        if canvas.ndim != 3 or canvas.shape[2] != 4:
            raise ValueError("canvas must be (h, w, 4) premultiplied BGRA")
        q = self.quad
        if q < _MIN_QUAD or self.opacity <= 0.0:
            return 0
        rad = _cached(self._rad, q, lambda: _Radial(q), _RAD_CACHE)
        tint = _cached(self._tint, (self.branch, q, self.will_charge), lambda: _Tint(rad, self.branch, self.will_charge),
                       _TINT_CACHE)
        cx, cy = self._resolve_center(forcefield)
        x0, y0 = int(round(cx)) - rad.hs, int(round(cy)) - rad.hs
        ch, cw = canvas.shape[:2]
        vx0, vy0 = max(x0, 0), max(y0, 0)
        vx1, vy1 = min(x0 + rad.side, cw), min(y0 + rad.side, ch)
        if vx0 >= vx1 or vy0 >= vy1:
            return 0
        sx, sy = slice(vx0 - x0, vx1 - x0), slice(vy0 - y0, vy1 - y0)

        idx = rad.idx[sy, sx]
        noise, u, v = self._sample(rad, sy, sx, self.rot)
        a = self._L.take(idx)                       # alpha profile (0..255 scale), noise not yet applied
        gain = self._NM.take(idx)
        gain *= noise
        gain += 1.0
        a *= gain                                   # inside `start`: originalAlpha *= lerp(1, 2.6, noise.r)
        if u is not None and tint.base_q is not None:
            q_ = rad.q
            bj = np.clip(np.floor(u * q_), 0, q_ - 1).astype(np.intp)
            bi = np.clip(np.floor(v * q_), 0, q_ - 1).astype(np.intp)
            rgb = tint.half[sy, sx] + np.float32(_BASE_MIX) * tint.base_q[bi, bj]
        else:
            rgb = tint.rgb[sy, sx]
        src = rgb * a[:, :, None]                   # premultiplied colour: .fx:38 rgb * originalAlpha * uOpacity
        np.minimum(src, 255.0, out=src)             # render-target clamp; rgb <= 1 so src <= alpha still holds
        np.minimum(a, 255.0, out=a)

        # source-over on premultiplied data: dst = src + dst * (1 - a), colour and alpha alike (never un-premultiply)
        reg = canvas[vy0:vy1, vx0:vx1]
        dst = reg.astype(np.float32)
        dst *= (1.0 - a * np.float32(1.0 / 255.0))[:, :, None]
        dst[:, :, :3] += src
        dst[:, :, 3] += a
        dst += 0.5
        np.minimum(dst, 255.0, out=dst)
        reg[...] = dst
        return int(np.count_nonzero(a >= 0.5))


# ----------------------------------------------------------------------------------------------
# Module-level convenience
# ----------------------------------------------------------------------------------------------
_SHARED = None
_T0 = perf_counter()


def draw_forcefield(canvas, **state):
    """One-call draw using a shared Forcefield (so the caches survive between frames). `state` takes the same keys as
    Forcefield.update(), plus optional 'scale' (world scale), 'time' (uTime in seconds; default = wall clock since
    import) and 'center' or 'x'/'y' (sphere centre; default = canvas centre). Returns pixels written."""
    global _SHARED
    h, w = canvas.shape[:2]
    scale = float(state.pop("scale", 1.0))
    t = state.pop("time", None)
    where = state.pop("center", None)
    if where is None and "x" in state and "y" in state:
        where = (state.pop("x"), state.pop("y"))
    ff = _SHARED
    if ff is None:
        ff = _SHARED = Forcefield((w, h), scale)
    ff.w, ff.h, ff.scale = w, h, scale
    ff.time = (perf_counter() - _T0) if t is None else float(t)
    ff.update(0.0, state)
    return ff.draw(canvas, where if where is not None else (w / 2.0, h / 2.0))