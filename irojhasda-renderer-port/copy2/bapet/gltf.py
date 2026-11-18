"""glTF / GLB file reader, texture helpers, and the Clip / Skin / Prim containers."""
from __future__ import annotations

import base64
import json
import re
import struct
from pathlib import Path
from urllib.parse import unquote

import numpy as np

from .config import HERE
from .mathutil import slerp


# =============================================================================
#  glTF / GLB loader
# =============================================================================
COMP = {5120: np.int8, 5121: np.uint8, 5122: np.int16, 5123: np.uint16, 5125: np.uint32, 5126: np.float32}

NCOMP = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT2": 4, "MAT3": 9, "MAT4": 16}

class GLTF:
    def __init__(self, path):
        path = Path(path)
        data = path.read_bytes()
        magic, _ver, _len = struct.unpack_from("<4sII", data, 0)
        if magic != b"glTF":
            raise ValueError("Not a binary glTF (.glb) file")
        # The BIN chunk is kept as a VIEW into the bytes just read. It used to be sliced out, which
        # copied nearly the whole file a second time (a 10 MB model was 20 MB for the length of the load).
        view = memoryview(data)
        off, self.bin, self.j = 12, b"", None
        while off + 8 <= len(data):
            clen, ctype = struct.unpack_from("<II", data, off); off += 8
            if ctype == 0x4E4F534A: self.j = json.loads(data[off:off + clen])     # json reads utf-8 bytes itself
            elif ctype == 0x004E4942: self.bin = view[off:off + clen]
            off += clen
        self.dir = path.parent
        self._bufs = {}

    def buffer(self, i):
        if i in self._bufs: return self._bufs[i]
        b = self.j["buffers"][i]
        uri = b.get("uri")
        if uri is None: data = self.bin
        elif uri.startswith("data:"): data = base64.b64decode(uri.split(",", 1)[1])
        else: data = (self.dir / unquote(uri)).read_bytes()
        self._bufs[i] = data
        return data

    def accessor(self, idx):
        a = self.j["accessors"][idx]
        dt = np.dtype(COMP[a["componentType"]]); n = NCOMP[a["type"]]; count = a["count"]
        if "bufferView" in a:
            bv = self.j["bufferViews"][a["bufferView"]]
            buf = self.buffer(bv["buffer"])
            start = bv.get("byteOffset", 0) + a.get("byteOffset", 0)
            esz = dt.itemsize * n
            stride = bv.get("byteStride") or esz
            if stride == esz:
                arr = np.frombuffer(buf, dtype=dt, count=count * n, offset=start).reshape(count, n)
            else:
                raw = np.frombuffer(buf, dtype=np.uint8)
                ix = start + np.arange(count)[:, None] * stride + np.arange(esz)[None, :]
                arr = np.ascontiguousarray(raw[ix]).view(dt).reshape(count, n)
        else:
            arr = np.zeros((count, n), dtype=dt)
        if a.get("normalized"):
            arr = arr.astype(np.float32)
            ct = a["componentType"]
            arr = {5120: np.maximum(arr / 127.0, -1), 5121: arr / 255.0,
                   5122: np.maximum(arr / 32767.0, -1), 5123: arr / 65535.0}.get(ct, arr)
        return arr.reshape(count) if n == 1 else arr

    def image_bytes(self, idx):
        im = self.j["images"][idx]
        if "bufferView" in im:
            bv = self.j["bufferViews"][im["bufferView"]]
            buf = self.buffer(bv["buffer"]); o = bv.get("byteOffset", 0)
            return bytes(buf[o:o + bv["byteLength"]])
        uri = im["uri"]
        if uri.startswith("data:"): return base64.b64decode(uri.split(",", 1)[1])
        return (self.dir / unquote(uri)).read_bytes()


_FOREIGN_IMG = {}


def foreign_image(glb_path, idx):
    """Raw bytes of image `idx` inside ANOTHER .glb, read once and kept.

    A character profile can point a primitive at a texture its own file does
    not ship - Iroha's tank hull has no baseColorTexture at all, and the real
    one is in the separately exported tank (see iroha.py). Kept out of
    Model.get_image's own cache so a borrowed image is read exactly once no
    matter how many primitives point at it.
    """
    key = (str(Path(glb_path).resolve()), int(idx))
    if key not in _FOREIGN_IMG:
        p = Path(glb_path)
        if not p.is_absolute(): p = HERE / p      # relative paths are relative to THIS file
        _FOREIGN_IMG[key] = GLTF(p).image_bytes(int(idx))
    return _FOREIGN_IMG[key]


def toks(name):
    return [t for t in re.split(r"[\s_\.\:\|\-]+", name.lower()) if t]

def norm_name(s):
    return re.sub(r"[^a-z0-9]", "", s.lower())

def compute_normals(pos, idx):
    tri = idx.reshape(-1, 3)
    p0, p1, p2 = pos[tri[:, 0]], pos[tri[:, 1]], pos[tri[:, 2]]
    fn = np.cross(p1 - p0, p2 - p0)
    n = np.zeros_like(pos, dtype=np.float64)
    for k in range(3): np.add.at(n, tri[:, k], fn)
    l = np.linalg.norm(n, axis=1, keepdims=True); l[l < 1e-12] = 1
    return (n / l).astype(np.float32)

def smooth_normals(pos, nrm):
    """Average normals of coincident vertices (so the outline hull has no cracks)."""
    ext = float((pos.max(0) - pos.min(0)).max()) or 1.0
    q = np.round((pos - pos.min(0)) / (ext * 1e-5)).astype(np.int64)
    key = (q[:, 0] << 34) | (q[:, 1] << 17) | q[:, 2]
    _, inv = np.unique(key, return_inverse=True)
    inv = inv.reshape(-1)
    acc = np.zeros((inv.max() + 1, 3), dtype=np.float64)
    np.add.at(acc, inv, nrm.astype(np.float64))
    out = acc[inv]
    l = np.linalg.norm(out, axis=1, keepdims=True); l[l < 1e-12] = 1
    return (out / l).astype(np.float32)


def bleed_alpha(buf, w, h, iters=6):
    """B2: flood RGB of fully transparent texels outwards, so mipmaps don't
    drag black into hair cards and lashes (dark halo)."""
    a = np.frombuffer(buf, np.uint8).reshape(h, w, 4).copy()
    solid = a[..., 3] > 8
    if solid.all() or not solid.any(): return buf
    rgb = a[..., :3].astype(np.float32)
    for _ in range(iters):
        acc = np.zeros_like(rgb); cnt = np.zeros(solid.shape, np.float32)
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            s = np.roll(solid, (dy, dx), (0, 1)); acc += np.roll(rgb, (dy, dx), (0, 1)) * s[..., None]; cnt += s
        fill = (~solid) & (cnt > 0)
        rgb[fill] = acc[fill] / cnt[fill, None]; solid |= fill
    a[..., :3] = rgb.astype(np.uint8)
    return a.tobytes()


class Clip:
    """One animation, baked to a fixed frame rate.

    Lazy: built with a `baker`, a clip is only a name and a duration until something reads its
    data. The first touch of T / Q / S / Wt / pin / origin / drift / ssig runs the baker, which
    fills all of them in, and from then on they are ordinary attributes (this hook is never
    reached again, so playback pays nothing for it).

    Baking is the expensive half of loading a model - a student ships 30-odd clips and the app
    plays a handful - so a model now pays for the clips it actually plays, when it first plays
    them, instead of for all of them up front (Model._bake_clip).

    Without a baker it is the plain container it always was.
    """
    _LAZY = frozenset(("T", "Q", "S", "Wt", "pin", "origin", "drift", "ssig"))

    def __init__(self, name, duration, T=None, Q=None, S=None, Wt=None, baker=None):
        self.name, self.duration = name, duration
        if baker is not None:
            self._baker = baker
            return
        self.T, self.Q, self.S = T, Q, S
        self.Wt = Wt                              # (frames, nodes) morph-target weight 0 per node
        self.drift = None
        self.ssig = None                          # log-scale signature of the structural bones
        self.pin = np.zeros((len(T), 3))     # per-frame offset that keeps the pelvis planted
        self.origin = np.zeros(3)            # frame-0 offset onto the idle pivot

    @property
    def baked(self):
        return "_baker" not in self.__dict__

    def __getattr__(self, name):
        # Only reached when the attribute is missing, i.e. before the bake.
        if name in Clip._LAZY:
            baker = self.__dict__.pop("_baker", None)
            if baker is not None:
                baker(self)
                try:
                    return self.__dict__[name]
                except KeyError:
                    pass
        raise AttributeError(f"'Clip' object has no attribute '{name}'")


class Skin:
    pass

class Prim:
    pass


def sample_plan(times, ts):
    """Which two keys each baked frame sits between, and how far along: (idx, u).

    Depends only on the key times, and nearly every channel of a clip is keyed on the same
    time accessor - so it is worked out once per accessor and shared (Model._bake_clip)
    instead of once per channel. Same arithmetic as sample_channel, line for line.
    """
    idx = np.clip(np.searchsorted(times, ts, side="right") - 1, 0, len(times) - 2)
    t0, t1 = times[idx], times[idx + 1]
    u = np.clip((ts - t0) / np.maximum(t1 - t0, 1e-9), 0, 1)[:, None]
    return idx, u


def sample_channels(vals, idx, u, step, rotation):
    """sample_channel for a whole stack of channels that share one time accessor.

    vals is (channels, keys, dim); returns (channels, frames, dim). Every operation is
    element-wise, so each channel comes out bit-for-bit what sample_channel gives it alone -
    it is only the per-call numpy overhead (x a few thousand channels per model) that goes.
    """
    if step: u = np.zeros_like(u)
    a, b = vals[:, idx], vals[:, idx + 1]
    if rotation: return slerp(a, b, u)
    return a + (b - a) * u


def sample_channel(times, vals, interp, ts, path):
    F = len(ts)
    if interp == "CUBICSPLINE" and len(vals) == 3 * len(times):
        vals = vals[1::3]
    if len(times) == 1:
        return np.repeat(vals[:1], F, axis=0)
    idx = np.clip(np.searchsorted(times, ts, side="right") - 1, 0, len(times) - 2)
    t0, t1 = times[idx], times[idx + 1]
    u = np.clip((ts - t0) / np.maximum(t1 - t0, 1e-9), 0, 1)[:, None]
    if interp == "STEP": u = np.zeros_like(u)
    a, b = vals[idx], vals[idx + 1]
    if path == "rotation": return slerp(a, b, u)
    return a + (b - a) * u
