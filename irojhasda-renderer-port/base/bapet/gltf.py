"""glTF / GLB file reader, texture helpers, and the Clip / Skin / Prim containers."""
from __future__ import annotations

import base64
import json
import re
import struct
from functools import lru_cache
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
        self.bin, self.j = b"", None
        with path.open("rb") as f:
            header = f.read(12)
            if len(header) != 12:
                raise ValueError("Truncated GLB header")
            magic, version, length = struct.unpack("<4sII", header)
            if magic != b"glTF" or version != 2 or length != path.stat().st_size:
                raise ValueError("Invalid binary glTF 2 header")
            while f.tell() < length:
                header = f.read(8)
                if len(header) != 8:
                    raise ValueError("Truncated GLB chunk header")
                size, kind = struct.unpack("<II", header)
                if size > length - f.tell():
                    raise ValueError("Truncated GLB chunk")
                if kind == 0x4E4F534A: self.j = json.loads(f.read(size))
                elif kind == 0x004E4942: self.bin = f.read(size)
                else: f.seek(size, 1)
        if self.j is None:
            raise ValueError("Missing GLB JSON chunk")
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
                arr = np.ndarray((count, n), dtype=dt, buffer=buf, offset=start,
                                 strides=(stride, dt.itemsize))
        else:
            arr = np.zeros((count, n), dtype=dt)
        if a.get("normalized"):
            arr = arr.astype(np.float32)
            ct = a["componentType"]
            divisor = {5120: 127.0, 5121: 255.0, 5122: 32767.0, 5123: 65535.0}.get(ct)
            if divisor:
                arr /= divisor
                if ct in (5120, 5122): np.maximum(arr, -1.0, out=arr)
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


@lru_cache(maxsize=4)
def foreign_image(glb_path, idx):
    path = Path(glb_path)
    return GLTF(path if path.is_absolute() else HERE / path).image_bytes(int(idx))


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
    def __init__(self, name, duration, T, Q, S, Wt=None):
        self.name, self.duration, self.T, self.Q, self.S = name, duration, T, Q, S
        self.Wt = Wt                              # (frames, nodes) morph-target weight 0 per node
        self.drift = None
        self.ssig = None                          # log-scale signature of the structural bones
        self.pin = np.zeros((len(T), 3))     # per-frame offset that keeps the pelvis planted
        self.origin = np.zeros(3)            # frame-0 offset onto the idle pivot


class Skin:
    pass

class Prim:
    pass


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
