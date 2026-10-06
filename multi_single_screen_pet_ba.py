#!/usr/bin/env python3
"""
Multi Pet - Single Screen  -  Blue Archive 3D pets (one per .glb)
===================================================================
Same as multi_pet.py, but every character is locked to ONE screen: they
only ever spawn on it, walk on it, jump on windows that sit on it, and are
clamped if you try to drag them off the edge.
================================================================
Loads EVERY *.glb file in the folder next to this script, creates one
independent character (own model, own rig, own animations, own dialogue)
for each of them, renders them with moderngl using a Blue-Archive-style
toon shader (cel ramp, tinted shadows, rim light, spec ticks, inverted-hull
outline), and shows them in transparent always-on-top PySide6 windows.

pip install PySide6 moderngl numpy

Uses ONLY these animations per model:
  Cafe_Idle, Cafe_Walk, Cafe_Reaction, Formation_Idle, Formation_Pickup,
  Exs_Cutin, Tactical_Start

Interaction
  cursor            head/body look at it everywhere, light follows it
  together          they glance at each other and hold conversations of their
                    own (topics, reactions, greetings); a nearby cursor always
                    wins over looking at another character
  click             poke  (Cafe_Reaction)
  double-click      EX cut-in (Exs_Cutin + screen banner)
  drag / throw      Formation_Pickup, pendulum swing, gravity, bounce, squash
  wiggle over head  headpat
  wheel             resize      shift+wheel / middle-drag / ctrl+drag  spin 3D
  right-click       menu for that character   tray icon + panel for the rest
  (Windows) they walk / jump / ride on top of your open windows, but only
            the ones on their screen

Picking the screen
  --screen N        use screen number N (0 = first in Qt's screen list)
  --screen cursor   use the screen the mouse is on at startup (default)
  --screen primary  use the primary screen
  Change it any time in the control panel (Characters tab).
"""
from __future__ import annotations

import sys, os, re, json, math, time, random, struct, base64, ctypes, argparse, traceback
from pathlib import Path
from urllib.parse import unquote
from collections import deque

if sys.platform.startswith("linux") and "QT_QPA_PLATFORM" not in os.environ:
    os.environ["QT_QPA_PLATFORM"] = "xcb"          # window positioning needs X11

try:
    import numpy as np
    import moderngl
    from PySide6 import QtCore, QtGui, QtWidgets
except ImportError as _e:                           # pragma: no cover
    print("Missing dependency:", _e)
    print("Run:  pip install PySide6 moderngl numpy")
    sys.exit(1)

from PySide6.QtCore import Qt, QTimer, QPointF, QRectF, QPoint, QSize

HERE = Path(__file__).resolve().parent
SETTINGS_PATH = HERE / "multi_single_screen_pet_ba_settings.json"   # its own settings file
BASE_W, BASE_H = 380, 520
BAKE_FPS = 60

# key -> (animation name, loops?)
ANIM_KEYS = {
    "idle":     ("Cafe_Idle", True),
    "walk":     ("Cafe_Walk", True),
    "alert":    ("Formation_Idle", True),
    "react":    ("Cafe_Reaction", False),
    "pickup":   ("Formation_Pickup", False),
    "cutin":    ("Exs_Cutin", False),
    "tactical": ("Tactical_Start", False),
}

DEFAULTS = dict(
    scale=1.0, walk_speed=70.0, fps=30, always_on_top=True, wander=True,
    look_at_cursor=True, follow_cursor=False, perch=True, speech=True,
    particles=True, click_through=True, yaw_offset=0.0, pos_x=None,
    positions={},            # character name -> saved feet X
    hidden=[],               # character names the user turned off
    peer_look=True,          # occasionally look at another character
    peer_look_rate=0.35,     # 0..1  how often she glances over
    chat=True,               # characters talk to each other
    chat_rate=0.5,           # 0..1  chattiness
    chat_meetup=True,        # walk over before talking
    screen="cursor",         # "cursor" | "primary" | "0", "1", ...  (one screen only)
    preset="Blue Archive",
    toon_threshold=0.46, toon_softness=0.07, shadow_strength=0.62, exposure=1.0,
    deep_shadow=0.25, rim=0.45, spec=0.05, saturation=1.08, outline_px=1.5, outline_dark=0.34,
    eyes_through_bangs=False,   # C4: off by default, see Renderer.draw
    light_follow=False, light_az=-30.0, light_el=38.0,
)

PRESETS = {
    "Blue Archive":   dict(toon_threshold=.46, toon_softness=.07,  shadow_strength=.62, exposure=1.0, deep_shadow=.25, rim=.45, spec=.05, saturation=1.08, outline_px=1.5, outline_dark=.34),
    "Soft Pastel":    dict(toon_threshold=.38, toon_softness=.14,  shadow_strength=.30, exposure=1.05, deep_shadow=.10, rim=.75, spec=.05, saturation=.95,  outline_px=1.1, outline_dark=.55),
    "Hard Cel":       dict(toon_threshold=.55, toon_softness=.006, shadow_strength=.85, exposure=1.0, deep_shadow=.35, rim=.35, spec=.30, saturation=1.20, outline_px=2.6, outline_dark=.15),
    "Night Patrol":   dict(toon_threshold=.52, toon_softness=.05,  shadow_strength=.90, exposure=.72, deep_shadow=.40, rim=1.10, spec=.20, saturation=.90, outline_px=2.0, outline_dark=.20),
    "Flat / Unshaded":dict(toon_threshold=.10, toon_softness=.05,  shadow_strength=.0,  exposure=1.0, deep_shadow=.0,  rim=.0,  spec=.0,  saturation=1.05, outline_px=1.5, outline_dark=.35),
}

# ------------------------------------------------------------------ dialogue
# Every character gets its own name (from the .glb file name) and its own pool
# of lines.  "{name}" is replaced with the character name when spoken.
POKE_LINES = ["Hm? Did you need something, Sensei?", "Ow... that's a little rude, you know.",
              "Please don't poke me. I'm trying to rest.", "Sensei, shouldn't you be working?",
              "...What? Is there something on my face?", "Fine, fine. I'm paying attention.",
              "If you're bored, I can keep you company. A little.",
              "{name}: reporting minor injury.", "Hey! Personal space, Sensei!",
              "...You again? Fine, I'm listening."]
HEADPAT_LINES = ["...Sensei? Why are you patting my head?", "H-hey... you're messing up my hair.",
                 "...It's not bad. Don't tell anyone I said that.", "Mmh... just a little longer, okay?",
                 "Careful, {name} doesn't do this for just anyone.",
                 "...Keep it up and I'll start charging rent."]
IDLE_LINES = ["...", "Is it time to go home yet?", "Sensei, you've been staring at that screen for a while.",
              "I'll keep watch. Quietly. From right here.", "Hm... I could use a nap.",
              "Don't forget to drink some water, Sensei.",
              "{name} is on standby.", "Still here. Still watching. Nothing much has happened.",
              "Do you want me to do something? I can walk, you know."]
HOVER_LINES = ["Sensei... you're staring.", "Is something wrong with me?",
               "Do you need something? You've been hovering for a while.",
               "{name} sees you hovering.", "Am I... on fire? Never mind."]
DRAG_LINES = ["W-wait, put me down!", "Eh?! Where are we going?", "Hey! I can walk by myself!",
              "Sensei, this is undignified.",
              "{name} can walk! Let me down, Sensei!", "Where are we taking {name}, Sensei?!"]
LAND_LINES = ["Ow... a little warning next time.", "...Landing: acceptable.", "That was rough, Sensei.",
              "{name} has filed a complaint about gravity.", "My knees. My everything."]
LOST_LINES = ["Whoa- the window moved!", "Eh? The floor disappeared!", "Sensei, the window went away!",
              "{name} lost her perch. Again."]
JUMP_LINES = ["Up we go.", "Found a nice spot.", "A better vantage point.",
              "{name} is ascending.", "Hup! ...That was higher than expected."]
CUTIN_LINE = "Alright. I'll take this seriously - just this once."
CUTIN_LINES = [CUTIN_LINE, "This is a one-time thing, {name} is serious now.",
               "Don't blink, Sensei. {name} is going all out."]
SUMMON_LINES = ["You called, Sensei?", "Reporting for duty.", "{name} is here.", "You summoned me?"]
PERCH_LINES = ["A good vantage point.", "From up here I can see everything.", "{name} has the high ground."]

# ------------------------------------------------------------------ pet-to-pet
# Conversations between two characters.  Each topic is a list of turns that are
# spoken in order, alternating speaker/listener.  Placeholders resolved at speak
# time:  {me} = the speaker, {other} = the listener, {name} = the speaker too.
# The last element of each turn is a tag: "plain", "reply" or "warm" (the
# listener answers "warm" turns with a heart particle now and then).
CHAT_TOPICS = {
    "greeting": [
        ("Oh, hello {other}. Didn't expect to see you here.", "plain"),
        ("Hi {me}. Busy as always?", "warm"),
        ("You're never far away, are you.", "reply"),
        ("Somewhere to be, {other}?", "plain"),
    ],
    "work": [
        ("Are you getting all this done today, {other}?", "plain"),
        ("Enough for today. Probably.", "reply"),
        ("I can help if you get stuck.", "warm"),
        ("Then take a break. Both of us.", "plain"),
        ("You always say that and then work anyway.", "reply"),
    ],
    "food": [
        ("I'm starving, {other}. When do we eat?", "plain"),
        ("Soon. Go be useful first.", "reply"),
        ("I'll save you something.", "warm"),
        ("That's the first sensible thing today.", "reply"),
    ],
    "weather": [
        ("Lovely weather, isn't it, {other}?", "plain"),
        ("Suspiciously nice. It won't last.", "reply"),
        ("Good day to be standing on a title bar.", "warm"),
        ("{other}, it's going to rain later.", "plain"),
    ],
    "gossip": [
        ("Heard something, {other}.", "plain"),
        ("Don't tell me.", "reply"),
        ("You were already going to.", "warm"),
        ("I already knew. Of course I did.", "reply"),
    ],
    "compliment": [
        ("You handled that well, {other}.", "plain"),
        ("It was nothing.", "warm"),
        ("I'm not sure I agree, but thank you.", "reply"),
        ("Say it again and I'll believe you.", "warm"),
    ],
    "rest": [
        ("Long day, {other}?", "plain"),
        ("Longest one yet.", "reply"),
        ("Sit down for a minute. Please.", "warm"),
        ("Sitting down is for other people.", "reply"),
    ],
    "weather2": [
        ("The light looks nice on you today, {other}.", "plain"),
        ("...Hm? Oh. Thank you.", "warm"),
        ("Don't make it weird.", "reply"),
        ("Too late. It's already weird.", "reply"),
    ],
}

# One-off reactions when the user does something while others are watching.
CHAT_AUDIENCE_LINES = {
    "poke": ["That's enough, {other}. Sensei.",
             "{other}, stop. You'll get us both in trouble.",
             "Hm? Am I interrupting something?",
             "Sensei, be gentler with {other}.",
             "Don't pick on {other}, Sensei.",
             "I saw that, {other}."],
    "headpat": ["{other}, your hair's a mess now.",
                "Hm? Are you getting that too, {other}?",
                "I want one as well. Don't tell anyone.",
                "Sensei, you always pick {other}."],
    "drag": ["Careful with {other}, Sensei.",
             "She's not a parcel, you know.",
             "Put {other} down gently...",
             "{other} doesn't look happy about that."],
}

# Shown in the panel status bar / while a conversation is running.
CHAT_TOPIC_LABELS = {
    "greeting": "greeting each other", "work": "comparing workloads",
    "food": "talking about food", "weather": "discussing the weather",
    "gossip": "sharing gossip", "compliment": "complimenting each other",
    "rest": "taking a break together", "weather2": "being awkward",
}

TWRAP = int(getattr(Qt.TextFlag.TextWordWrap, "value", Qt.TextFlag.TextWordWrap))


# =============================================================================
#  math helpers (column-vector convention, numpy 4x4)
# =============================================================================
def clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v

def rot_x(a):
    c, s = math.cos(a), math.sin(a)
    return np.array([[1, 0, 0], [0, c, -s], [0, s, c]], dtype=np.float64)

def rot_y(a):
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]], dtype=np.float64)

def m4(R=None, t=None):
    M = np.eye(4)
    if R is not None: M[:3, :3] = R
    if t is not None: M[:3, 3] = t
    return M

def translate(t):
    return m4(None, np.asarray(t, dtype=np.float64))

def rot_z4(a):
    M = np.eye(4); c, s = math.cos(a), math.sin(a)
    M[0, 0] = c; M[0, 1] = -s; M[1, 0] = s; M[1, 1] = c
    return M

def scale4(x, y, z):
    return np.diag([x, y, z, 1.0])

def perspective(fovy, aspect, n, f):
    t = 1.0 / math.tan(fovy / 2.0)
    M = np.zeros((4, 4))
    M[0, 0] = t / aspect; M[1, 1] = t
    M[2, 2] = (f + n) / (n - f); M[2, 3] = 2 * f * n / (n - f); M[3, 2] = -1.0
    return M

def mat_bytes(M):
    return np.ascontiguousarray(np.asarray(M, dtype=np.float32).T).tobytes()

def quat_to_rot(q):
    x, y, z, w = q[:, 0], q[:, 1], q[:, 2], q[:, 3]
    R = np.empty((len(q), 3, 3))
    R[:, 0, 0] = 1 - 2 * (y * y + z * z); R[:, 0, 1] = 2 * (x * y - z * w); R[:, 0, 2] = 2 * (x * z + y * w)
    R[:, 1, 0] = 2 * (x * y + z * w); R[:, 1, 1] = 1 - 2 * (x * x + z * z); R[:, 1, 2] = 2 * (y * z - x * w)
    R[:, 2, 0] = 2 * (x * z - y * w); R[:, 2, 1] = 2 * (y * z + x * w); R[:, 2, 2] = 1 - 2 * (x * x + y * y)
    return R

def compose_trs(T, Q, S):
    n = len(T)
    M = np.zeros((n, 4, 4))
    M[:, :3, :3] = quat_to_rot(Q) * S[:, None, :]
    M[:, :3, 3] = T
    M[:, 3, 3] = 1.0
    return M

def pelvis_track(chain, T, Q, S):
    """World position of the pelvis bone through every frame of a clip.

    Only the ancestor chain is composed (a handful of nodes) instead of the whole
    skeleton, so this stays cheap even for long clips.
    """
    W = None
    for nd in chain:                                  # T/Q/S are (F, n_nodes, ...)
        q, t, s = Q[:, nd], T[:, nd], S[:, nd]
        L = np.zeros((len(t), 4, 4))
        L[:, :3, :3] = quat_to_rot(q) * s[:, None, :]
        L[:, :3, 3] = t
        L[:, 3, 3] = 1.0
        W = L if W is None else np.matmul(W, L)
    return W[:, :3, 3]                               # (F, 3)

def mat_to_quat(R):
    tr = R[0, 0] + R[1, 1] + R[2, 2]
    if tr > 0:
        s = math.sqrt(tr + 1.0) * 2; w = .25 * s
        x = (R[2, 1] - R[1, 2]) / s; y = (R[0, 2] - R[2, 0]) / s; z = (R[1, 0] - R[0, 1]) / s
    elif R[0, 0] > R[1, 1] and R[0, 0] > R[2, 2]:
        s = math.sqrt(1.0 + R[0, 0] - R[1, 1] - R[2, 2]) * 2
        w = (R[2, 1] - R[1, 2]) / s; x = .25 * s; y = (R[0, 1] + R[1, 0]) / s; z = (R[0, 2] + R[2, 0]) / s
    elif R[1, 1] > R[2, 2]:
        s = math.sqrt(1.0 + R[1, 1] - R[0, 0] - R[2, 2]) * 2
        w = (R[0, 2] - R[2, 0]) / s; x = (R[0, 1] + R[1, 0]) / s; y = .25 * s; z = (R[1, 2] + R[2, 1]) / s
    else:
        s = math.sqrt(1.0 + R[2, 2] - R[0, 0] - R[1, 1]) * 2
        w = (R[1, 0] - R[0, 1]) / s; x = (R[0, 2] + R[2, 0]) / s; y = (R[1, 2] + R[2, 1]) / s; z = .25 * s
    q = np.array([x, y, z, w]); return q / np.linalg.norm(q)

def decompose(m):
    t = m[:3, 3].copy()
    sc = np.linalg.norm(m[:3, :3], axis=0)
    sc[sc < 1e-12] = 1.0
    if np.linalg.det(m[:3, :3]) < 0: sc[0] = -sc[0]
    return t, mat_to_quat(m[:3, :3] / sc), sc

def slerp(q0, q1, t):
    d = np.sum(q0 * q1, axis=-1, keepdims=True)
    q1 = np.where(d < 0, -q1, q1)
    d = np.clip(np.abs(d), 0.0, 1.0)
    th = np.arccos(d); sn = np.sin(th)
    near = sn < 1e-5
    den = np.where(near, 1.0, sn)
    a = np.where(near, 1.0 - t, np.sin((1.0 - t) * th) / den)
    b = np.where(near, t, np.sin(t * th) / den)
    q = a * q0 + b * q1
    return q / np.maximum(np.linalg.norm(q, axis=-1, keepdims=True), 1e-12)


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
        off, self.bin, self.j = 12, b"", None
        while off + 8 <= len(data):
            clen, ctype = struct.unpack_from("<II", data, off); off += 8
            chunk = data[off:off + clen]; off += clen
            if ctype == 0x4E4F534A: self.j = json.loads(chunk.decode("utf-8"))
            elif ctype == 0x004E4942: self.bin = chunk
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


class Model:
    def __init__(self, path):
        g = GLTF(path); self.g = g; j = g.j
        nodes = j.get("nodes", []); N = self.N = len(nodes)
        self.names = [n.get("name", f"node{i}") for i, n in enumerate(nodes)]
        self.parent = np.full(N, -1, dtype=np.int64)
        for i, n in enumerate(nodes):
            for c in n.get("children", []): self.parent[c] = i
        T = np.zeros((N, 3)); Q = np.tile([0, 0, 0, 1.0], (N, 1)); S = np.ones((N, 3))
        for i, n in enumerate(nodes):
            if "matrix" in n:
                t, q, s = decompose(np.array(n["matrix"], dtype=np.float64).reshape(4, 4).T)
            else:
                t = n.get("translation", [0, 0, 0]); q = n.get("rotation", [0, 0, 0, 1]); s = n.get("scale", [1, 1, 1])
            T[i], Q[i], S[i] = t, np.asarray(q) / max(np.linalg.norm(q), 1e-12), s
        self.rest_T, self.rest_Q, self.rest_S = T, Q, S
        depth = np.zeros(N, dtype=np.int64)
        for i in range(N):
            d, p, guard = 0, self.parent[i], 0
            while p >= 0 and guard < N: d += 1; p = self.parent[p]; guard += 1
            depth[i] = d
        self.depth = depth
        nchild = np.zeros(N, dtype=np.int64)
        for i in range(N):
            if self.parent[i] >= 0: nchild[self.parent[i]] += 1
        self.is_leaf = nchild == 0                 # leaves are props / weapons, not rig structure
        self.levels = [np.where(depth == d)[0] for d in range(int(depth.max()) + 1)] if N else []

        # ---- skins ----
        self.skins, base = [], 0
        for s in j.get("skins", []):
            sk = Skin(); sk.joints = np.array(s["joints"], dtype=np.int64)
            if "inverseBindMatrices" in s:
                sk.ibm = g.accessor(s["inverseBindMatrices"]).astype(np.float64).reshape(-1, 4, 4).transpose(0, 2, 1)
            else:
                sk.ibm = np.tile(np.eye(4), (len(sk.joints), 1, 1))
            sk.base = base; base += len(sk.joints); self.skins.append(sk)
        self.total_joints = max(base, 1)

        # ---- meshes ----
        self.img_cache = {}
        self.prims = []
        # Morph weights: Blue Archive exports every mouth shape as its own mesh and
        # picks one by animating a blend-shape weight (0 = shown, 1 = collapsed to a point).
        self.node_w0 = np.zeros(N)
        for ni, n in enumerate(nodes):
            ws = n.get("weights") or (j["meshes"][n["mesh"]].get("weights") if "mesh" in n else None)
            if ws: self.node_w0[ni] = ws[0]
        self.node_w = self.node_w0.copy()
        for ni, n in enumerate(nodes):
            if "mesh" not in n: continue
            mesh = j["meshes"][n["mesh"]]
            for pr in mesh["primitives"]:
                if pr.get("mode", 4) != 4: continue
                at = pr["attributes"]
                if "POSITION" not in at: continue
                P = Prim()
                pos = g.accessor(at["POSITION"]).astype(np.float32); V = len(pos)
                idx = g.accessor(pr["indices"]).astype(np.uint32).reshape(-1) if "indices" in pr else np.arange(V, dtype=np.uint32)
                idx = idx[: len(idx) // 3 * 3]
                nrm = g.accessor(at["NORMAL"]).astype(np.float32) if "NORMAL" in at else compute_normals(pos, idx)
                uv = g.accessor(at["TEXCOORD_0"]).astype(np.float32) if "TEXCOORD_0" in at else np.zeros((V, 2), np.float32)
                skinned = n.get("skin") is not None and "JOINTS_0" in at and "WEIGHTS_0" in at
                if skinned:
                    jn = g.accessor(at["JOINTS_0"]).astype(np.float32)
                    w = g.accessor(at["WEIGHTS_0"]).astype(np.float32)
                    sm = w.sum(1); z = sm < 1e-6
                    w[z] = [1, 0, 0, 0]; sm[z] = 1.0; w /= sm[:, None]
                else:
                    jn = np.zeros((V, 4), np.float32); w = np.tile(np.array([1, 0, 0, 0], np.float32), (V, 1))
                P.pos, P.nrm, P.uv, P.joints, P.weights, P.idx = pos, nrm, uv, jn, w, idx
                P.onrm = smooth_normals(pos, nrm)
                P.node = ni; P.skin = n["skin"] if skinned else -1
                P.base = self.skins[P.skin].base if skinned else 0
                mi = pr.get("material")
                mat = j["materials"][mi] if mi is not None else {}
                pbr = mat.get("pbrMetallicRoughness", {})
                P.basecol = tuple(pbr.get("baseColorFactor", [1, 1, 1, 1]))
                P.amode = {"OPAQUE": 0, "MASK": 1, "BLEND": 2}.get(mat.get("alphaMode", "OPAQUE"), 0)
                P.cutoff = mat.get("alphaCutoff", 0.5)
                P.src = None; P.repeat = (True, True)
                ti = pbr.get("baseColorTexture")
                if ti is not None:
                    tex = j["textures"][ti["index"]]
                    src = tex.get("source")
                    if src is None:
                        for ext in tex.get("extensions", {}).values():
                            if "source" in ext: src = ext["source"]; break
                    P.src = src
                    if tex.get("sampler") is not None and "samplers" in j:
                        sp = j["samplers"][tex["sampler"]]
                        P.repeat = (sp.get("wrapS", 10497) != 33071, sp.get("wrapT", 10497) != 33071)
                P.name = " ".join([mat.get("name", ""), mesh.get("name", ""), n.get("name", "")]).lower()
                unlit = any(k in P.name for k in ("halo", "glow", "effect"))
                face = any(k in P.name for k in ("face", "eye", "mouth", "brow", "cheek"))
                P.shade = 0.0 if unlit else (0.7 if face else 1.0)
                P.outline = (not unlit) and P.amode != 2 and not any(k in P.name for k in ("eye", "brow", "mouth", "cheek", "lash"))
                P.see_through = any(k in P.name for k in ("eye", "brow", "lash"))
                P.hide_morph = False
                if pr.get("targets") and "POSITION" in pr["targets"][0]:
                    dpos = g.accessor(pr["targets"][0]["POSITION"]).astype(np.float32)
                    ext = float(np.ptp(pos + dpos, axis=0).max())
                    P.hide_morph = ext < 1e-4 * max(float(np.ptp(pos, axis=0).max()), 1e-9)
                self.prims.append(P)

        # ---- rest pose, bones ----
        W0 = self.pose_world(T, Q, S); self.W0 = W0
        self.height = max(float(np.ptp(self.posed_positions(W0)[:, 1])), 1e-6) if self.prims else 1.0
        self.head = self.find_bone(("head",))
        self.neck = self.find_bone(("neck",))
        self.spine = self.find_bone(("spine1",)) or self.find_bone(("spine",))
        self.pelvis = self.find_bone(("pelvis", "hips", "hip"))
        if self.pelvis is None:
            jset = set(int(x) for sk in self.skins for x in sk.joints)
            for jn_ in sorted(jset, key=lambda k: depth[k]):
                if self.parent[jn_] not in jset: self.pelvis = jn_; break
        if self.pelvis is None: self.pelvis = 0
        # ancestors of the pelvis: the only bones whose scale moves the whole body
        self.pelvis_chain, _nd = [], int(self.pelvis)
        while _nd >= 0:
            self.pelvis_chain.append(_nd); _nd = int(self.parent[_nd])
        self.pelvis_chain.reverse()
        lb = [(b, w) for b, w in ((self.spine, .12), (self.neck, .28), (self.head, .60)) if b is not None]
        tot = sum(w for _, w in lb) or 1.0
        self.look_bones = [(b, w / tot) for b, w in lb] if self.head is not None else []
        self.face_yaw = 0.0
        for i, nm in enumerate(self.names):
            if "toe" in nm.lower() and self.parent[i] >= 0:
                v = W0[i][:3, 3] - W0[self.parent[i]][:3, 3]
                if math.hypot(v[0], v[2]) > 1e-4 * self.height:
                    phi = math.atan2(v[0], v[2])
                    self.face_yaw = -round(phi / (math.pi / 2)) * (math.pi / 2)
                break

        for P in self.prims: self.soften_head(P)

        # ---- animations (baked to fixed rate) ----
        self.clips = {}
        for ai, a in enumerate(j.get("animations", [])):
            name = a.get("name", f"anim{ai}")
            chans, dur = [], 0.0
            for ch in a["channels"]:
                tg = ch["target"]
                if "node" not in tg or tg["path"] not in ("translation", "rotation", "scale", "weights"): continue
                sm = a["samplers"][ch["sampler"]]
                times = g.accessor(sm["input"]).astype(np.float64).reshape(-1)
                vals = g.accessor(sm["output"]).astype(np.float64)
                chans.append((tg["node"], tg["path"], times, vals, sm.get("interpolation", "LINEAR")))
                dur = max(dur, float(times[-1]))
            dur = max(dur, 1 / 30)
            F = max(2, int(math.ceil(dur * BAKE_FPS)) + 1)
            ts = np.linspace(0, dur, F)
            cT = np.repeat(T[None], F, 0); cQ = np.repeat(Q[None], F, 0); cS = np.repeat(S[None], F, 0)
            cW = np.repeat(self.node_w0[None], F, 0)
            for node, path, times, vals, interp in chans:
                if path == "weights":
                    nt = max(1, vals.size // (len(times) * (3 if interp == "CUBICSPLINE" else 1)))
                    cW[:, node] = sample_channel(times, vals.reshape(-1, nt), interp, ts, path)[:, 0]
                    continue
                v = sample_channel(times, vals, interp, ts, path)
                if path == "translation": cT[:, node] = v
                elif path == "rotation": cQ[:, node] = v
                else: cS[:, node] = v
            self.clips[name] = Clip(name, dur, cT, cQ, cS, cW)
        self.rest_clip = Clip("rest", 1.0, np.repeat(T[None], 2, 0), np.repeat(Q[None], 2, 0), np.repeat(S[None], 2, 0),
                               np.repeat(self.node_w0[None], 2, 0))
        for c in list(self.clips.values()) + [self.rest_clip]:
            P = pelvis_track(self.pelvis_chain, c.T, c.Q, c.S)
            d = P[-1] - P[0]; d[1] = 0
            c.drift = d if np.linalg.norm(d) > 0.02 * self.height else None   # does this clip have root motion?
            # Pin the pelvis instead of de-trending it linearly: Iroha's Exs_Cutin sits
            # still for 3.8s and then travels 7 units, and a linear correction overshoots
            # by several body heights mid-clip. pin[t] exactly cancels the travelled XZ,
            # so the feet stay planted and the offset is constant -> nothing to pop.
            pin = -(P - P[0]); pin[:, 1] = 0.0
            c.pin = pin
            # Some rigs embed a second skeleton using compensating bone scales
            # (Iroha: Tank_Move at 0.001 and character_root at 1000). Those only cancel
            # inside a single clip, so two clips with different scale rigs cannot be
            # blended at all. Only the pelvis chain counts - weapon/prop bones scaled
            # 0.01 <-> 1.0 are ordinary show/hide keys, not a rig mismatch.
            c.ssig = np.log(np.maximum(c.S[:, self.pelvis_chain].mean(axis=0).mean(axis=1), 1e-9))
        # A1 (optional step): many clips are authored away from the idle pivot - some
        # sideways, Hoshino's Exs_Cutin starts 1.7 body-heights low - so they jump on
        # their first frame. Give every clip the same starting pivot. Y is included:
        # it lines the character up with the floor the app positions her on, while all
        # motion *within* the clip (bob, crouch, leap) is untouched because pin zeroes Y.
        idle_ref = self.find_clip("Cafe_Idle") or self.rest_clip
        ref = pelvis_track(self.pelvis_chain, idle_ref.T[0:1], idle_ref.Q[0:1], idle_ref.S[0:1])[0].copy()
        for c in list(self.clips.values()) + [self.rest_clip]:
            c.origin = ref - pelvis_track(self.pelvis_chain, c.T[0:1], c.Q[0:1], c.S[0:1])[0]

        # ---- B2: only bleed textures used exclusively by MASK/BLEND prims ----
        used_a = {p.src for p in self.prims if p.amode != 0}
        used_o = {p.src for p in self.prims if p.amode == 0}
        self.bleed_srcs = used_a - used_o

    # ---------------------------------------------------------------
    def soften_head(self, P, amount=0.85):
        """C3: push head-vertex normals toward a sphere for the soft anime face.

        Skin only. The eye / brow / mouth / cheek meshes have their shading painted
        into the texture, so re-lighting them toward a sphere washes the irises out to
        flat grey. Runs after P.onrm exists, so the outline hull keeps the real normals.
        """
        if self.head is None or P.skin < 0 or P.shade == 0.0: return
        if "hair" in P.name or any(k in P.name for k in ("eye", "brow", "mouth", "cheek", "lash")):
            return
        hj = np.where(self.skins[P.skin].joints == self.head)[0]
        if len(hj) == 0: return
        wh = (P.weights * (P.joints.astype(np.int64) == hj[0])).sum(1)
        m = wh > 0.6
        if m.sum() < 30: return
        d = P.pos - P.pos[m].mean(0)
        d /= np.maximum(np.linalg.norm(d, axis=1, keepdims=True), 1e-6)
        n = P.nrm[m] * (1 - amount) + d[m] * amount
        P.nrm[m] = n / np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-6)

    # ---------------------------------------------------------------
    def get_image(self, src):
        if src is None: return None
        if src in self.img_cache: return self.img_cache[src]
        res = None
        try:
            qi = QtGui.QImage.fromData(self.g.image_bytes(src))
            if qi.isNull(): raise ValueError("decode failed")
            qi = qi.convertToFormat(QtGui.QImage.Format.Format_RGBA8888)
            w, h, bpl = qi.width(), qi.height(), qi.bytesPerLine()
            buf = bytes(qi.constBits())
            if bpl != w * 4:
                buf = np.frombuffer(buf, np.uint8).reshape(h, bpl)[:, : w * 4].tobytes()
            res = (w, h, buf[: w * h * 4])
            if src in self.bleed_srcs: res = (w, h, bleed_alpha(res[2], w, h))
        except Exception as e:
            print("[warn] image", src, "failed:", e)
        self.img_cache[src] = res
        return res

    def find_bone(self, words):
        for i, n in enumerate(self.names):
            tk = toks(n)
            if tk and tk[-1] in words: return i
        return None

    def find_clip(self, token):
        t = norm_name(token); names = list(self.clips)
        for n in names:
            if norm_name(n) == t: return self.clips[n]
        c = [n for n in names if norm_name(n).endswith(t)] or [n for n in names if t in norm_name(n)]
        return self.clips[min(c, key=len)] if c else None

    def pose_world(self, T, Q, S, post=None):
        L = compose_trs(T, Q, S); W = np.empty_like(L)
        pm = {}
        if post:
            for node, R in post: pm.setdefault(int(self.depth[node]), []).append((node, R))
        for d, idx in enumerate(self.levels):
            if d == 0: W[idx] = L[idx]
            else: W[idx] = np.matmul(W[self.parent[idx]], L[idx])
            for node, R in pm.get(d, ()):
                p = W[node][:3, 3].copy()
                D = np.eye(4); D[:3, :3] = R; D[:3, 3] = p - R @ p
                W[node] = D @ W[node]
        return W

    def posed_positions(self, W, skip_fx=False):
        out = []
        for p in self.prims:
            if skip_fx and p.shade == 0.0: continue      # halos / glows / effects must not set the scale
            ph = np.concatenate([p.pos.astype(np.float64), np.ones((len(p.pos), 1))], 1)
            if p.skin >= 0:
                sk = self.skins[p.skin]; bm = np.matmul(W[sk.joints], sk.ibm)
                acc = np.zeros((len(ph), 4))
                for k in range(4):
                    ji = np.clip(p.joints[:, k].astype(np.int64), 0, len(bm) - 1)
                    acc += p.weights[:, k:k + 1] * np.einsum("vij,vj->vi", bm[ji], ph)
            else:
                acc = ph @ W[p.node].T
            out.append(acc[:, :3])
        return np.concatenate(out)

    def compute_fit(self, W):
        pts = self.posed_positions(W, skip_fx=True)
        if len(pts) == 0: pts = self.posed_positions(W)      # everything is an effect prim
        lo, hi = np.percentile(pts, 0.2, axis=0), np.percentile(pts, 99.8, axis=0)
        pel = W[self.pelvis][:3, 3]
        return dict(cx=float(pel[0]), cz=float(pel[2]), miny=float(lo[1]), maxy=float(hi[1]), H=float(hi[1] - lo[1]))

    def dump(self):
        print(f"nodes={self.N} skins={len(self.skins)} joints={self.total_joints} prims={len(self.prims)}")
        print("head/neck/spine/pelvis:", [self.names[i] if i is not None else None for i in (self.head, self.neck, self.spine, self.pelvis)])
        print("face_yaw(deg):", math.degrees(self.face_yaw))
        print("animations:")
        for n, c in self.clips.items(): print(f"  {n}  ({c.duration:.2f}s, root-motion={'yes' if c.drift is not None else 'no'})")
        print("primitives:")
        for p in self.prims:
            print(f"  {p.name.strip()!r} verts={len(p.pos)} skin={p.skin} alpha={p.amode} tex={p.src} shade={p.shade}")
        self.diagnose()

    def diagnose(self):
        """A1/A2/A4 report: which clips drift, start off-centre, or key scale to zero."""
        h = self.height
        idle = self.find_clip("Cafe_Idle") or self.rest_clip
        ref = self.pose_world(idle.T[0], idle.Q[0], idle.S[0])[self.pelvis][:3, 3]
        for n, c in self.clips.items():
            w0 = self.pose_world(c.T[0], c.Q[0], c.S[0])[self.pelvis][:3, 3]
            w1 = self.pose_world(c.T[-1], c.Q[-1], c.S[-1])[self.pelvis][:3, 3]
            drift = math.hypot(w1[0] - w0[0], w1[2] - w0[2]) / h * 100
            start = math.hypot(w0[0] - ref[0], w0[2] - ref[2]) / h * 100
            hidden = [self.names[i] for i in np.where(c.S.min(axis=(0, 2)) < 0.05)[0]]
            print(f"{n:22s} drift={drift:5.1f}%h root-motion={'yes' if c.drift is not None else 'no '} "
                  f"start-offset={start:5.1f}%h zero-scale={hidden[:6]}")


# =============================================================================
#  Animation player with cross-fade
# =============================================================================
class Rig:
    def __init__(self, model):
        self.m = model; self.clip = None; self.t = 0.0; self.loop = True; self.speed = 1.0
        self.done = False; self.fade = 1.0; self.fade_dur = 0.15; self.prev = None; self.cur_pose = None
        self.world = model.W0
        self.cur_wt = model.node_w0

    def play(self, clip, loop, speed=1.0, fade=0.15, restart=True):
        if clip is None: return
        if (not restart) and clip is self.clip:
            self.loop, self.speed = loop, speed; return
        self.prev = self.cur_pose
        self.fade = 0.0 if (self.prev is not None and fade > 0) else 1.0
        self.fade_dur = max(fade, 1e-3)
        # A hard cut beats sliding through garbage when the two clips disagree about
        # bone scale (see Model: c.ssig).
        if self.prev is not None and self.clip is not None \
                and clip.ssig is not None and self.clip.ssig is not None \
                and float(np.abs(clip.ssig - self.clip.ssig).max()) > 0.5:
            self.prev = None; self.fade = 1.0
        self.clip, self.t, self.loop, self.speed, self.done = clip, 0.0, loop, speed, False

    def advance(self, dt):
        c = self.clip
        if c is None: return
        self.t += dt * self.speed
        if self.loop:
            if c.duration > 0: self.t %= c.duration
        elif self.t >= c.duration:
            self.t = c.duration; self.done = True
        if self.fade < 1.0: self.fade = min(1.0, self.fade + dt / self.fade_dur)

    def sample(self):
        """Current pose + its root-motion offset.

        Nothing is blended here. Cross-fading happens in *world* space in evaluate():
        lerping local TRS only works when the hierarchy is a plain chain, and Iroha's
        is not - her pelvis is a descendant of her head bone and 'character_root'
        carries a 364-unit local translation that only cancels once composed. Blending
        those locals sends the pelvis several body heights off screen.
        """
        c = self.clip; F = c.T.shape[0]
        f = min(self.t / c.duration, 1.0) * (F - 1) if c.duration > 0 else 0.0
        i0 = min(int(f), F - 2); u = f - i0
        T = c.T[i0] * (1 - u) + c.T[i0 + 1] * u
        S = c.S[i0] * (1 - u) + c.S[i0 + 1] * u
        Q = slerp(c.Q[i0], c.Q[i0 + 1], np.full((c.Q.shape[1], 1), u))
        # A1: the root-motion pin rides along with the pose so it cross-fades too,
        # instead of snapping on the first frame of the new clip.
        off = c.origin + c.pin[i0] * (1 - u) + c.pin[i0 + 1] * u
        self.cur_pose = (T, Q, S, off)
        if c.Wt is not None: self.cur_wt = c.Wt[i0]
        return T, Q, S, off

    def _look(self, yaw, pitch):
        m = self.m; post = []
        if m.look_bones and (abs(yaw) > 1e-4 or abs(pitch) > 1e-4):
            Rf = rot_y(m.face_yaw)                        # B6: pitch axis follows the model's facing
            for node, w in m.look_bones:
                post.append((node, rot_y(yaw * w) @ Rf.T @ rot_x(-pitch * w) @ Rf))
        return post

    def evaluate(self, yaw, pitch):
        m = self.m; T, Q, S, off = self.sample()
        m.node_w = self.cur_wt
        post = self._look(yaw, pitch)
        W = m.pose_world(T, Q, S, post)
        if self.prev is not None and self.fade < 1.0:
            k = self.fade * self.fade * (3 - 2 * self.fade)
            pT, pQ, pS, pOff = self.prev
            # A2: a leaf bone scaled to 0 is a weapon/prop show-hide key, not an
            # animation. Hand the previous pose the current scale so it never ramps
            # through a visible grow/shrink (and so zero-scale normals can't go NaN).
            # Leaves only: structural bones are never show/hide keys.
            hide = np.minimum(pS, S).min(axis=1) < 0.05
            hide &= m.is_leaf
            if hide.any():
                pS = pS.copy(); pS[hide] = S[hide]
            W = m.pose_world(pT, pQ, pS, post) * (1 - k) + W * k
            off = pOff * (1 - k) + off * k               # A1: offset cross-fades with the pose
        elif self.fade >= 1.0:
            self.prev = None
        W[:, :3, 3] += off
        self.world = W
        return W


# =============================================================================
#  Renderer: moderngl, MSAA, GPU skinning, Blue Archive style toon shader
# =============================================================================
VS_COMMON = """
#version 330
uniform mat4 u_proj, u_view, u_model, u_node;
uniform sampler2D u_bones;
uniform int u_skinned, u_base;
in vec3 in_pos; in vec3 in_nrm; in vec2 in_uv; in vec4 in_joints; in vec4 in_weights; in vec3 in_onrm;
mat4 bone(int j){
    int r = u_base + j;
    return mat4(texelFetch(u_bones, ivec2(0, r), 0), texelFetch(u_bones, ivec2(1, r), 0),
                texelFetch(u_bones, ivec2(2, r), 0), texelFetch(u_bones, ivec2(3, r), 0));
}
mat4 skinMat(){
    if (u_skinned == 0) return u_node;
    return in_weights.x * bone(int(in_joints.x + 0.5)) + in_weights.y * bone(int(in_joints.y + 0.5))
         + in_weights.z * bone(int(in_joints.z + 0.5)) + in_weights.w * bone(int(in_joints.w + 0.5));
}
"""

VS_MAIN = VS_COMMON + """
out vec3 v_nrm; out vec3 v_wpos; out vec2 v_uv;
void main(){
    mat4 sk = skinMat();
    vec4 wp = u_model * sk * vec4(in_pos, 1.0);
    vec3 n = mat3(u_model) * mat3(sk) * in_nrm;
    float nl = length(n);
    n = nl > 1e-5 ? n / nl : vec3(0.0);      // zero-scale bone would give NaN
    v_nrm = n; v_wpos = wp.xyz; v_uv = in_uv;
    gl_Position = u_proj * u_view * wp;
}
"""

FS_MAIN = """
#version 330
uniform sampler2D u_tex;
uniform vec4 u_basecol;
uniform int u_alpha_mode;
uniform float u_cutoff, u_shade, u_fade;
uniform vec3 u_light, u_campos, u_light_col, u_shadow_tint, u_rim_col;
uniform float u_thresh, u_soft, u_shadow, u_rim, u_spec, u_sat, u_deep;
in vec3 v_nrm; in vec3 v_wpos; in vec2 v_uv;
out vec4 f_col;

vec3 shadowOf(vec3 c){
    vec3 s = mix(c, c * c, 0.55);               // deeper but keeps saturation
    return mix(c, s * u_shadow_tint, u_shadow);
}
void main(){
    vec4 t = texture(u_tex, v_uv) * u_basecol;
    if (u_alpha_mode == 1 && t.a < u_cutoff) discard;
    if (u_alpha_mode == 2 && t.a < 0.06) discard;
    // cutout is all-or-nothing: letting partial alpha through wrote depth on
    // edge fragments and left a faint see-through fringe where hair overlaps skin
    float a = ((u_alpha_mode == 2) ? t.a : 1.0) * u_fade;
    vec3 base = t.rgb;
    float nl = length(v_nrm);
    vec3 N = nl > 1e-5 ? v_nrm / nl : vec3(0.0, 0.0, 1.0);
    vec3 V = normalize(u_campos - v_wpos);
    vec3 L = normalize(u_light);
    float ndl = dot(N, L);
    float hl = mix(1.0, ndl * 0.5 + 0.5, u_shade);
    float lit  = smoothstep(u_thresh - u_soft, u_thresh + u_soft, hl);
    float deep = smoothstep(u_thresh * 0.45 - u_soft, u_thresh * 0.45 + u_soft, hl);
    vec3 sh = shadowOf(base);
    vec3 col = mix(mix(sh, shadowOf(sh), u_deep), sh, deep);    // u_deep = 0 disables the 3rd band
    col = mix(col, base * u_light_col, lit);
    float fres = 1.0 - clamp(dot(N, V), 0.0, 1.0);
    float rim = smoothstep(0.62, 0.85, fres) * smoothstep(-0.15, 0.45, ndl) * u_rim * u_shade;
    col += (1.0 - col) * u_rim_col * rim * 0.6;                 // screen-style, never clips
    vec3 H = normalize(L + V);
    col += vec3(smoothstep(0.50, 0.55, pow(max(dot(N, H), 0.0), 48.0))) * u_spec * u_shade * u_light_col;
    float l = dot(col, vec3(0.299, 0.587, 0.114));
    col = clamp(mix(vec3(l), col, u_sat), 0.0, 1.0);
    f_col = vec4(col * a, a);
}
"""

VS_OUT = VS_COMMON + """
uniform float u_width;
out vec2 v_uv;
void main(){
    mat4 sk = skinMat();
    vec4 wp = u_model * sk * vec4(in_pos, 1.0);
    vec3 n = mat3(u_model) * mat3(sk) * in_onrm;
    float nl = length(n);
    n = nl > 1e-5 ? n / nl : vec3(0.0);
    vec4 vp = u_view * wp;
    vec3 nh = mat3(u_model) * mat3(sk) * in_nrm;
    float nhl = length(nh);
    float facing = nhl > 1e-5 ? dot(nh / nhl, normalize(-vp.xyz)) : 0.0;
    vp.xy += n.xy * u_width;      // screen-space inflate along the smoothed normal
    vp.z  -= u_width * 2.0 + max(facing, 0.0) * u_width * 40.0;   // extra push where the surface
                                                                // faces us: kills speckles between
                                                                // layered cloth/hair without speckling
                                                                // mirrored bones like a winding test
    v_uv = in_uv;
    gl_Position = u_proj * vp;
}
"""

FS_OUT = """
#version 330
uniform sampler2D u_tex;
uniform vec4 u_basecol;
uniform int u_alpha_mode;
uniform float u_cutoff, u_dark;
in vec2 v_uv;
out vec4 f_col;
void main(){
    vec4 t = texture(u_tex, v_uv) * u_basecol;
    if (u_alpha_mode == 1 && t.a < u_cutoff) discard;
    vec3 c = mix(t.rgb, t.rgb * t.rgb, 0.6) * u_dark * 1.5 * vec3(0.92, 0.94, 1.0);
    f_col = vec4(c, 1.0);
}
"""

def U(prog, name, val):
    try: u = prog[name]
    except KeyError: return
    if isinstance(val, (bytes, bytearray)): u.write(val)
    else: u.value = val


class Renderer:
    FRAC, PADB, FOV = 0.58, 0.07, math.radians(16)

    def __init__(self, model, fit, ctx=None):
        self.m, self.fit = model, fit
        ctx = self.ctx = ctx if ctx is not None else moderngl.create_standalone_context(require=330)
        self.pg = ctx.program(vertex_shader=VS_MAIN, fragment_shader=FS_MAIN)
        self.po = ctx.program(vertex_shader=VS_OUT, fragment_shader=FS_OUT)
        self.bone_tex = ctx.texture((4, model.total_joints), 4, dtype="f4")
        self.bone_tex.filter = (moderngl.NEAREST, moderngl.NEAREST)
        self.bone_tex.write(np.tile(np.eye(4, dtype="f4").T, (model.total_joints, 1, 1)).tobytes())
        white = ctx.texture((1, 1), 4, b"\xff\xff\xff\xff")
        tex_cache = {}
        for p in model.prims:
            img = model.get_image(p.src)
            key = (p.src, p.repeat)
            if img is None: p.tex = white
            elif key in tex_cache: p.tex = tex_cache[key]
            else:
                tx = ctx.texture((img[0], img[1]), 4, img[2])
                tx.build_mipmaps(); tx.filter = (moderngl.LINEAR_MIPMAP_LINEAR, moderngl.LINEAR)
                try: tx.anisotropy = 8.0
                except Exception: pass
                tx.repeat_x, tx.repeat_y = p.repeat
                p.tex = tex_cache[key] = tx
            data = np.hstack([p.pos, p.nrm, p.uv, p.joints, p.weights, p.onrm]).astype("f4")
            vbo = ctx.buffer(data.tobytes()); ibo = ctx.buffer(p.idx.astype("u4").tobytes())
            fmt = "3f 3f 2f 4f 4f 3f"
            names = ("in_pos", "in_nrm", "in_uv", "in_joints", "in_weights", "in_onrm")
            p.vao = ctx.vertex_array(self.pg, [(vbo, fmt, *names)], ibo, skip_errors=True)
            p.vao_o = ctx.vertex_array(self.po, [(vbo, fmt, *names)], ibo, skip_errors=True)
        self.prims = sorted(model.prims, key=lambda q: q.amode)
        self.fbo = self.fbo_rs = None
        self.w = self.h = 0
        H = fit["H"]; V = H / self.FRAC
        self.dist = V / (2 * math.tan(self.FOV / 2))
        cy = fit["miny"] + V / 2 - self.PADB * V
        self.eye = np.array([fit["cx"], cy, fit["cz"] + self.dist])
        self.view = translate(-self.eye)
        self.V_world = V
        self.PV = np.eye(4)

    def set_size(self, w, h):
        if (w, h) == (self.w, self.h): return
        ctx = self.ctx
        for o in (self.fbo, self.fbo_rs):
            if o is not None: o.release()
        self.w, self.h = w, h
        try:
            col = ctx.renderbuffer((w, h), 4, samples=4); dep = ctx.depth_renderbuffer((w, h), samples=4)
            self.fbo = ctx.framebuffer(color_attachments=[col], depth_attachment=dep)
        except Exception:
            col = ctx.renderbuffer((w, h), 4); dep = ctx.depth_renderbuffer((w, h))
            self.fbo = ctx.framebuffer(color_attachments=[col], depth_attachment=dep)
        self.fbo_rs = ctx.framebuffer(color_attachments=[ctx.renderbuffer((w, h), 4)])
        d = self.dist; H = self.fit["H"]
        self.proj = perspective(self.FOV, w / h, max(d - H * 2.0, d * 0.05), d + H * 3.0)
        self.world_per_px = self.V_world / h

    def project(self, p3, M):
        v = self.PV @ (M @ np.array([p3[0], p3[1], p3[2], 1.0]))
        x, y = v[0] / v[3], v[1] / v[3]
        return (x * 0.5 + 0.5) * self.w, (1 - (y * 0.5 + 0.5)) * self.h

    def draw(self, world, M, light, cfg):
        ctx, m = self.ctx, self.m
        if m.skins:
            rows = [np.matmul(world[sk.joints], sk.ibm).transpose(0, 2, 1) for sk in m.skins]
            self.bone_tex.write(np.ascontiguousarray(np.concatenate(rows).astype("f4")).tobytes())
        self.fbo.use(); self.fbo.clear(0.0, 0.0, 0.0, 0.0, depth=1.0)
        ctx.enable(moderngl.DEPTH_TEST | moderngl.BLEND)
        ctx.disable(moderngl.CULL_FACE)
        ctx.blend_func = moderngl.ONE, moderngl.ONE_MINUS_SRC_ALPHA
        self.PV = self.proj @ self.view
        self.bone_tex.use(1)
        for pg in (self.pg, self.po):
            U(pg, "u_proj", mat_bytes(self.proj)); U(pg, "u_view", mat_bytes(self.view)); U(pg, "u_model", mat_bytes(M))
            U(pg, "u_bones", 1)
        pg = self.pg
        lc = np.array([1.03, 1.01, 0.98]) * cfg["exposure"]
        U(pg, "u_light", tuple(float(x) for x in light)); U(pg, "u_campos", tuple(float(x) for x in self.eye))
        U(pg, "u_light_col", tuple(float(x) for x in lc))
        U(pg, "u_shadow_tint", (0.74, 0.78, 0.96)); U(pg, "u_rim_col", (0.72, 0.88, 1.0))
        U(pg, "u_thresh", float(cfg["toon_threshold"])); U(pg, "u_soft", max(float(cfg["toon_softness"]), 1e-4))
        U(pg, "u_shadow", float(cfg["shadow_strength"])); U(pg, "u_rim", float(cfg["rim"]))
        U(pg, "u_spec", float(cfg["spec"])); U(pg, "u_sat", float(cfg["saturation"]))
        U(pg, "u_deep", float(cfg["deep_shadow"]))
        U(pg, "u_tex", 0)
        U(self.po, "u_tex", 0)
        U(self.po, "u_width", float(cfg["outline_px"]) * self.world_per_px)
        U(self.po, "u_dark", float(cfg["outline_dark"]))
        nw = m.node_w
        shown = lambda p: not (p.hide_morph and nw[p.node] >= 0.5)
        opaque = [p for p in self.prims if p.amode < 2 and shown(p)]
        blends = [p for p in self.prims if p.amode == 2 and shown(p)]
        for p in opaque: self._draw(p, world, False)
        if cfg["outline_px"] > 0.01:
            for p in opaque:
                if p.outline: self._draw(p, world, True)
        # C4: eyes drawn again with an inverted depth test, so they read through the bangs.
        # Off by default: with no stencil to separate the layers this shows the eye
        # mesh's *hidden* backing geometry through the face rather than the visible
        # eye, which greys the eyes out. Only worth trying on a model whose eyes are
        # a single flat layer.
        eyes = [p for p in opaque if getattr(p, "see_through", False)] if cfg.get("eyes_through_bangs") else []
        if eyes:
            ctx.depth_func = '>'; self.fbo.depth_mask = False
            for p in eyes: self._draw(p, world, False, fade=0.4)
            ctx.depth_func = '<='; self.fbo.depth_mask = True
        try: self.fbo.depth_mask = False
        except Exception: pass
        for p in blends: self._draw(p, world, False)
        try: self.fbo.depth_mask = True
        except Exception: pass
        ctx.copy_framebuffer(self.fbo_rs, self.fbo)
        return self.fbo_rs.read(components=4, alignment=1)

    def _draw(self, p, world, outline, fade=1.0):
        prog = self.po if outline else self.pg
        p.tex.use(0)
        U(prog, "u_skinned", 1 if p.skin >= 0 else 0); U(prog, "u_base", int(p.base))
        U(prog, "u_node", mat_bytes(world[p.node]) if p.skin < 0 else mat_bytes(np.eye(4)))
        U(prog, "u_basecol", tuple(float(x) for x in p.basecol))
        U(prog, "u_alpha_mode", int(p.amode)); U(prog, "u_cutoff", float(p.cutoff))
        if not outline:
            U(prog, "u_shade", float(p.shade)); U(prog, "u_fade", float(fade))
        (p.vao_o if outline else p.vao).render()


# =============================================================================
#  Windows: top-of-window perches (ctypes, no extra deps)
# =============================================================================
class WinTracker:
    SKIP = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd", "Windows.UI.Core.CoreWindow",
            "XamlExplorerHostIslandWindow", "ApplicationFrameInputSinkWindow", "TopLevelWindowForOverflowXamlIsland"}

    def __init__(self):
        self.enabled = sys.platform == "win32"; self.wins = []
        if self.enabled:
            try:
                from ctypes import wintypes
                self.wt = wintypes
                self.u32 = ctypes.windll.user32; self.dwm = ctypes.windll.dwmapi
                self.u32.IsWindowVisible.argtypes = [wintypes.HWND]
                self.u32.IsIconic.argtypes = [wintypes.HWND]
                self.u32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
                self.u32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
                self.u32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
                self.u32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
                self.dwm.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
                self.proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
                self.pid = os.getpid()
            except Exception:
                self.enabled = False

    def refresh(self, dpr, top_limit):
        if not self.enabled: return
        wt, out = self.wt, []
        cls = ctypes.create_unicode_buffer(256)
        def cb(hwnd, _lp):
            try:
                if not self.u32.IsWindowVisible(hwnd) or self.u32.IsIconic(hwnd): return True
                if self.u32.GetWindowTextLengthW(hwnd) == 0: return True
                pid = wt.DWORD(0); self.u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if pid.value == self.pid: return True
                self.u32.GetClassNameW(hwnd, cls, 256)
                if cls.value in self.SKIP: return True
                cloaked = ctypes.c_int(0)
                self.dwm.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(cloaked), 4)
                if cloaked.value: return True
                r = wt.RECT()
                if self.dwm.DwmGetWindowAttribute(hwnd, 9, ctypes.byref(r), ctypes.sizeof(r)) != 0:
                    self.u32.GetWindowRect(hwnd, ctypes.byref(r))
                l, t, rr, b = r.left / dpr, r.top / dpr, r.right / dpr, r.bottom / dpr
                if rr - l < 160 or b - t < 80 or t < top_limit + 60: return True
                out.append((int(hwnd) if hwnd else 0, l, t, rr, b))
            except Exception:
                pass
            return True
        try:
            self.u32.EnumWindows(self.proc(cb), 0)
            self.wins = out
        except Exception:
            self.enabled = False

    def rect(self, hwnd):
        for w in self.wins:
            if w[0] == hwnd: return w[1:]
        return None

    def surfaces(self, fx):
        res = []
        for i, (h, l, t, r, b) in enumerate(self.wins):
            if l + 6 <= fx <= r - 6:
                covered = any(l2 <= fx <= r2 and t2 <= t + 3 <= b2 for (_, l2, t2, r2, b2) in self.wins[:i])
                if not covered: res.append((t, h))
        return res


# =============================================================================
#  EX cut-in overlay (full-screen, click-through)
# =============================================================================
class Cutin(QtWidgets.QWidget):
    def __init__(self):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint |
                         Qt.WindowType.Tool | Qt.WindowType.WindowTransparentForInput)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.active = False; self.t0 = 0.0; self.dur = 2.0; self.anchor = (0, 0); self.who = ""

    def start(self, geom, ax, ay, dur, who=""):
        self.setGeometry(geom.x(), geom.y(), geom.width(), geom.height() - 1)
        self.anchor = (ax - geom.x(), ay - geom.y()); self.t0 = time.time(); self.dur = dur
        self.who = (who or "").upper()
        self.active = True; self.show()

    def step(self):
        if time.time() - self.t0 > self.dur: self.active = False; self.hide()
        else: self.update()

    def paintEvent(self, _e):
        if not self.active: return
        t = time.time() - self.t0
        W, H = self.width(), self.height()
        a = min(1.0, t / 0.18) * min(1.0, max(0.0, (self.dur - t) / 0.35))
        p = QtGui.QPainter(self); p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QtGui.QColor(4, 14, 40, int(125 * a)))
        ax, ay = self.anchor
        rnd = random.Random(int(t * 24))
        for _ in range(46):                                    # speed lines
            ang = rnd.uniform(0, math.tau); r0 = H * rnd.uniform(0.22, 0.4); r1 = H * rnd.uniform(0.7, 1.3)
            p.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255, int(rnd.uniform(40, 120) * a)), rnd.uniform(1, 3)))
            p.drawLine(QPointF(ax + math.cos(ang) * r0, ay + math.sin(ang) * r0),
                       QPointF(ax + math.cos(ang) * r1, ay + math.sin(ang) * r1))
        slide = 1 - (1 - min(1.0, t / 0.30)) ** 3
        p.save(); p.translate(W / 2, max(H * 0.2, min(H * 0.8, ay))); p.rotate(-9)
        bw, bh = W * 1.7, H * 0.17
        x = -bw / 2 + (1 - slide) * (-W * 1.3)
        g = QtGui.QLinearGradient(x, 0, x + bw, 0)
        g.setColorAt(0.0, QtGui.QColor(10, 70, 190, 0)); g.setColorAt(0.15, QtGui.QColor(18, 120, 255, int(225 * a)))
        g.setColorAt(0.75, QtGui.QColor(70, 200, 255, int(225 * a))); g.setColorAt(1.0, QtGui.QColor(70, 200, 255, 0))
        p.fillRect(QRectF(x, -bh / 2, bw, bh), QtGui.QBrush(g))
        for yy, hh in ((-bh / 2 - 7, 4), (bh / 2 + 3, 4)):
            p.fillRect(QRectF(x + bw * 0.1, yy, bw * 0.8, hh), QtGui.QColor(255, 255, 255, int(235 * a)))
        f1 = QtGui.QFont("Arial Black"); f1.setPixelSize(int(H * 0.085)); f1.setItalic(True); f1.setBold(True)
        tx = -W * 0.30 + (1 - slide) * W * 0.6
        path = QtGui.QPainterPath(); path.addText(tx, bh * 0.08, f1, "EX SKILL")
        p.strokePath(path, QtGui.QPen(QtGui.QColor(8, 40, 120, int(255 * a)), 9, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.fillPath(path, QtGui.QColor(255, 255, 255, int(255 * a)))
        f2 = QtGui.QFont("Arial"); f2.setPixelSize(int(H * 0.03)); f2.setBold(True); f2.setItalic(True)
        p.setFont(f2); p.setPen(QtGui.QColor(225, 245, 255, int(255 * a)))
        p.drawText(QPointF(tx + 8, bh * 0.40), self.who or "EX SKILL")
        p.restore()
        if t < 0.14:
            p.fillRect(self.rect(), QtGui.QColor(255, 255, 255, int(200 * (1 - t / 0.14))))
        p.end()


# =============================================================================
#  The pet window
# =============================================================================
def heart_path(s):
    p = QtGui.QPainterPath(); p.moveTo(0, s * 0.8)
    p.cubicTo(-s * 1.1, s * 0.1, -s * 0.7, -s * 0.9, 0, -s * 0.35)
    p.cubicTo(s * 0.7, -s * 0.9, s * 1.1, s * 0.1, 0, s * 0.8)
    return p

def star_path(r):
    poly = QtGui.QPolygonF()
    for i in range(8):
        rr = r if i % 2 == 0 else r * 0.22; a = i * math.pi / 4
        poly.append(QPointF(math.cos(a) * rr, math.sin(a) * rr))
    p = QtGui.QPainterPath(); p.addPolygon(poly); p.closeSubpath(); return p


class Pet(QtWidgets.QWidget):
    PADB = Renderer.PADB
    LOOPS = ("idle", "walk", "alert")

    def __init__(self, A, ch):
        super().__init__()
        self.A, self.cfg = A, A.cfg
        self.ch = ch                                     # this character's data
        self.name = ch.name
        self.model = ch.model
        self.clips = ch.clips
        self.rig = Rig(ch.model)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setMouseTracking(True)
        self.px = self.py = 0.0; self.vx = self.vy = 0.0
        self.W, self.H, self.dpr, self.G = 100, 100, 1.0, 2600.0
        self.grounded = False; self.support = None; self.bounces = 0
        self.state = "idle"; self.state_t = 0.0; self.state_dur = 0.0; self.shot_key = None
        self.anim_key = "idle"; self.air_kind = "jump"
        self.walk_dir = 1; self.edge_decided = False; self.step_off = False
        self.plan_t = 2.0; self.chat_t = random.uniform(25, 60)
        self.dragging = False; self.press_g = None; self.press_win = QPoint(); self.rotating = False
        self.rot_yaw0 = 0.0; self.hist = deque(maxlen=12); self.drag_started_say = 0.0
        self.gx = self.gy = 0.0; self.body_yaw = 0.0; self.head_yaw = self.head_pitch = 0.0
        self.roll = self.roll_v = 0.0; self.sq = self.sq_v = 0.0; self.zoom = 1.0
        self.spin_t = -1.0; self.bow = 0.0; self.last_px = 0.0
        self.light = np.array([-0.5, 0.6, 0.8])
        self.head_px = (BASE_W / 2, BASE_H * 0.3)
        self.bubble = None; self.parts = []
        # --- pet-to-pet ---
        self.gaze_peer = None; self.gaze_left = 0.0; self.gaze_cd = random.uniform(3.0, 9.0)
        self.chat_until = 0.0; self.chat_with = None; self.chat_topic = ""
        self.chat_next = 0.0; self.chat_lines = None; self.chat_i = 0; self.meet_target = None
        self.chat_lead = False
        self.last_topic = ""; self.avoid = {}          # topic -> cool-down time (chat avoids repeats)
        self.hover = False; self.hover_time = 0.0; self.hover_said = False
        self.pat_sign = 0; self.pat_events = []; self.pat_cool = 0.0; self.last_cur = QPoint()
        self.frame_n = 0; self.mask_dirty = True; self.mask_key = None
        self._pt = False; self.dt = 1 / 30.0
        self.img = None; self.alpha = None; self.raw = None
        self.bfont = QtGui.QFont(); self.bfont.setBold(True)
        self.apply_flags(show=False)
        self.apply_scale()
        self.play("idle")

    # ---------------- geometry helpers ----------------
    def feet_pos(self): return self.px + self.W / 2, self.py + self.H * (1 - self.PADB)

    def set_feet(self, fx, fy):
        self.px = fx - self.W / 2; self.py = fy - self.H * (1 - self.PADB)
        self.move(int(round(self.px)), int(round(self.py)))

    def apply_flags(self, show=True):
        f = Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
        if self.cfg["always_on_top"]: f |= Qt.WindowType.WindowStaysOnTopHint
        vis = self.isVisible()
        self.setWindowFlags(f)
        self._pt = False        # recreating the window resets the ex-style
        if vis or show: self.show()

    def set_passthrough(self, on):
        """A3: on Windows let the window manager do hit-testing per pixel instead
        of an every-3rd-frame mask region, which clipped fast limbs and swallowed
        the heart / star / puff particles."""
        if sys.platform != "win32" or on == self._pt or not self.isVisible(): return
        self._pt = on
        try:
            u = ctypes.windll.user32
            u.GetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int]; u.GetWindowLongW.restype = ctypes.c_long
            u.SetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_long]
            hwnd = int(self.winId())
            st = u.GetWindowLongW(hwnd, -20)                             # GWL_EXSTYLE
            u.SetWindowLongW(hwnd, -20, (st | 0x80020) if on else (st & ~0x20))
        except Exception: pass

    def apply_scale(self):
        s = self.cfg["scale"]; fx, fy = self.feet_pos()
        self.W, self.H = int(BASE_W * s), int(BASE_H * s)
        scr = self.screen()
        self.dpr = scr.devicePixelRatio() if scr else 1.0
        self.G = 2600.0 * max(0.6, s)
        self.bfont.setPointSizeF(max(8.0, 9.5 * math.sqrt(s)))
        self.setFixedSize(self.W, self.H)
        self.ch.renderer.set_size(max(32, int(round(self.W * self.dpr))), max(32, int(round(self.H * self.dpr))))
        self.set_feet(fx, fy); self.mask_dirty = True

    # ---------------- dialogue ----------------
    def line(self, pool):
        return random.choice(pool).replace("{name}", self.name)

    def spawn(self, index=0):
        g = self.A.resolve_screen()
        saved = self.cfg["positions"].get(self.name) if isinstance(self.cfg.get("positions"), dict) else None
        if saved is None and index == 0:
            saved = self.cfg["pos_x"]
        if saved is None:                                       # spread them out on the one screen
            n = max(1, len(self.A.pets))
            frac = 0.9 - index * min(0.75 / n, 0.25)
            saved = g.left() + (g.right() - g.left()) * frac
        fx = self.A.clamp_x(saved, self.W)
        self.set_feet(fx, self.A.floor_y(fx)); self.grounded = True
        self.show(); self.do_tactical(first=True)

    # ---------------- animation / state ----------------
    def play(self, key, restart=True):
        name, loop = ANIM_KEYS[key]
        self.anim_key = key
        self.rig.play(self.clips.get(key), loop, 1.0, 0.08 if key == "pickup" else 0.16, restart)

    def enter(self, state, key=None, dur=0.0):
        prev_key = self.anim_key
        self.state, self.state_t, self.state_dur = state, 0.0, dur
        if state == "shot": self.shot_key = key; k = key
        elif state == "drag": k = "pickup"
        elif state == "fall": k = "pickup" if self.air_kind == "thrown" else "alert"
        else: k = state
        self.play(k, restart=(state not in self.LOOPS or k != prev_key))

    def plan_next(self): self.plan_t = random.uniform(2.5, 7.0)

    def say(self, text, dur=None):
        if not self.cfg["speech"]: return
        self.bubble = (text, time.time(), dur or (2.4 + 0.05 * len(text))); self.mask_dirty = True

    def emit(self, kind, n, x=None, y=None, spread=70):
        if not self.cfg["particles"]: return
        if x is None: x, y = self.head_px
        sc = self.cfg["scale"]
        for _ in range(n):
            ang = random.uniform(-math.pi * 0.95, -math.pi * 0.05); sp = random.uniform(40, 170) * sc
            self.parts.append(dict(kind=kind, x=x + random.uniform(-spread, spread) * 0.5 * sc, y=y + random.uniform(-10, 10),
                                   vx=math.cos(ang) * sp, vy=math.sin(ang) * sp, life=0.0, max=random.uniform(0.9, 1.6),
                                   size=random.uniform(7, 13) * math.sqrt(sc), rot=random.uniform(-0.5, 0.5)))

    # ---- pet-to-pet helpers ----
    def peers(self, max_dist=None, same_surface=False):
        """Other visible pets, nearest first. same_surface = perched on the same window."""
        return self.A.peers_of(self, max_dist, same_surface)

    def greet_near(self, other):
        """Short two-line hello between this pet and `other` (no distance check)."""
        if other is None or other is self: return False
        if self.busy_chat() or other.busy_chat(): return False
        if not (self.free() and other.free()): return False
        now = time.time()
        topic = "greeting"; lines = random.sample(CHAT_TOPICS[topic], 2)
        cool = now + random.uniform(60, 150)
        self.avoid[topic] = other.avoid[topic] = cool
        self.last_topic = other.last_topic = topic
        self.chat_lines = other.chat_lines = lines
        self.chat_i = other.chat_i = 0
        self.chat_with = other; other.chat_with = self
        self.chat_lead = True; other.chat_lead = False      # exactly one pet drives the turns
        self.chat_topic = other.chat_topic = topic
        self.chat_next = other.chat_next = now + random.uniform(0.2, 0.7)
        self.gaze_peer = other; self.gaze_left = 5.0
        other.gaze_peer = self; other.gaze_left = 5.0
        return True

    def greet(self):
        """One-line hello to whoever is nearest - used when two characters pass each other."""
        for q in self.peers(max_dist=self.W * 0.85):
            if q.in_chat() or q.busy_chat() or q.dragging: continue
            return self.greet_near(q)
        return False

    def free(self):
        """True when she can start / continue a conversation (not busy, not mid-shot)."""
        return (self.cfg["chat"] and not self.dragging and self.grounded
                and self.state != "shot" and self.bubble is None)

    def in_chat(self, now=None):
        now = time.time() if now is None else now
        return now < self.chat_until and self.chat_with is not None

    def busy_chat(self):
        """True while a conversation is running (or about to) - blocks wander plans."""
        return self.chat_lines is not None or self.meet_target is not None

    def chat_pick_topic(self, other=None):
        """A topic neither of them used recently."""
        now = time.time(); pool = []
        for t in CHAT_TOPICS:
            if self.avoid.get(t, 0.0) > now: continue
            if other is not None and other.avoid.get(t, 0.0) > now: continue
            pool.append(t)
        return random.choice(pool) if pool else random.choice(list(CHAT_TOPICS))

    def chat_say(self, text, listener=None):
        """Speak a pet-to-pet line, resolving {me} / {other} / {name}."""
        me = self.name
        other = listener.name if listener is not None else me
        text = (text.replace("{me}", me).replace("{other}", other).replace("{name}", me))
        self.say(text)
        self.gaze_peer = listener if listener is not None else None
        self.gaze_left = max(self.gaze_left, 3.4)
        if listener is not None and not listener.in_chat() and listener.state in ("idle", "walk", "alert"):
            listener.gaze_peer = self; listener.gaze_left = max(listener.gaze_left, 3.4)

    def chat_step(self, now):
        """Say the next line of the conversation. Returns False once it is over."""
        if self.chat_lines is None: return False
        other = self.chat_with
        if other is None or not self.chat_lead: return False     # the follower just listens
        if not other.isVisible() or not self.isVisible():
            self.chat_end(); return False
        if now < self.chat_next: return True
        if self.chat_i >= len(self.chat_lines):
            self.chat_end(); return False
        text, tag = self.chat_lines[self.chat_i]
        # even turn = self speaks, odd turn = the other pet replies
        speak, listen = (self, other) if self.chat_i % 2 == 0 else (other, self)
        self.chat_i += 1
        gap = 1.4 if self.chat_i == 1 else random.uniform(2.6, 5.0)
        self.chat_next = now + gap
        speak.chat_say(text, listen)
        if listen.state in ("idle", "alert", "walk"): listen.enter("alert", dur=gap + 2.6)
        if tag == "warm" and random.random() < 0.5: listen.emit("heart", 3)
        if random.random() < 0.35: speak.emit("star", 2)
        # both stay locked in for the whole conversation
        for p in (self, other): p.chat_until = max(p.chat_until, self.chat_next + 1.2)
        return True

    def chat_end(self):
        """Finish the conversation and let both get back to normal."""
        other = self.chat_with
        for p in (self, other):
            if p is None: continue
            p.chat_lines = None; p.chat_with = None; p.chat_i = 0; p.chat_next = 0.0
            p.chat_lead = False; p.chat_until = 0.0; p.meet_target = None
            if p.gaze_peer is self: p.gaze_peer = None; p.gaze_left = 0.0
            if p.state == "alert" and not p.dragging and p.grounded: p.enter("idle"); p.plan_next()
        self.chat_lines = None; self.chat_with = None; self.chat_topic = ""

    def chat_abort(self, why=""):
        """Something else happened (drag / poke / cut-in) - drop out of the chat."""
        was = self.chat_lines is not None or self.chat_with is not None or self.meet_target is not None
        if not was: return
        if why and self.cfg["speech"]: self.say(why, 2.0)
        self.chat_end(); self.meet_target = None

    def audience(self, verb="poke"):
        """A nearby pet comments on what the user just did to this one."""
        if not self.cfg["chat"] or len(self.A.pets) < 2: return False
        pool = CHAT_AUDIENCE_LINES.get(verb)
        if not pool: return False
        now = time.time()
        for q in self.peers(max_dist=self.W * 2.6):
            if q.dragging or not q.grounded or q.in_chat(now) or q.state == "shot": continue
            q.chat_say(random.choice(pool), self)
            if q.state in ("idle", "walk", "alert"): q.enter("alert", dur=4.0)
            q.gaze_left = max(q.gaze_left, 3.4)
            if verb == "headpat" and random.random() < 0.45: q.emit("heart", 3)
            elif random.random() < 0.2: q.emit("star", 2)
            return True
        return False

    # ---- actions (used by menu / panel / mouse) ----
    def poke(self):
        if not self.grounded or self.dragging: return
        self.enter("shot", "react"); self.say(self.line(POKE_LINES)); self.emit("star", 7)
        self.sq_v += 1.2
        self.audience("poke")

    def headpat(self):
        if not self.grounded or self.dragging: return
        self.enter("shot", "react"); self.say(self.line(HEADPAT_LINES)); self.emit("heart", 9); self.bow = 1.0
        self.audience("headpat")

    def do_cutin(self):
        if not self.grounded or self.dragging: return
        self.chat_abort()
        self.enter("shot", "cutin"); self.say(self.line(CUTIN_LINES), 3.0)
        scr = self.screen().geometry() if self.screen() else QtWidgets.QApplication.primaryScreen().geometry()
        c = self.clips.get("cutin"); dur = clamp(c.duration if c else 2.0, 1.8, 4.0)
        self.A.cutin.start(scr, self.px + self.head_px[0], self.py + self.head_px[1], dur, self.name)
        self.raise_(); self.emit("star", 14, spread=160)

    def do_tactical(self, first=False):
        if not self.grounded or self.dragging: return
        start = f"{self.name}, reporting in. ...Do I really have to work, Sensei?" if first \
            else "Tactical start. ...Let's get this over with."
        self.enter("shot", "tactical"); self.say(start, 3.6)

    def spin(self): self.spin_t = 0.0

    def launch(self):
        self.grounded = False; self.support = None; self.dragging = False
        self.vx = random.choice([-1, 1]) * random.uniform(500, 1400) * self.cfg["scale"]
        self.vy = -random.uniform(1500, 2300) * self.cfg["scale"]
        self.air_kind = "thrown"; self.bounces = 0; self.enter("fall"); self.say("Eeeh?! Sensei!?", 1.8)

    def recall(self):
        """Called when the user summons this specific character."""
        self.say(self.line(SUMMON_LINES), 2.0)

    def play_anim(self, key):
        if self.dragging or not self.grounded: return
        if key == "cutin": self.do_cutin()
        elif key == "idle": self.enter("idle"); self.plan_t = 8
        elif key == "walk": self.walk_dir = random.choice([-1, 1]); self.enter("walk", dur=5.0)
        elif key == "alert": self.enter("alert", dur=6.0)
        else: self.enter("shot", key)

    def reset_position(self):
        g = self.A.resolve_screen()
        fx = self.A.clamp_x(g.right() - self.W * 0.9, self.W)
        self.support = None; self.grounded = True; self.dragging = False; self.vx = self.vy = 0
        self.set_feet(fx, self.A.floor_y(fx)); self.enter("idle")

    def try_jump(self):
        fx, fy = self.feet_pos(); s = self.cfg["scale"]; cand = []
        gh = self.A.resolve_screen()
        for (h, l, t, r, b) in self.A.tracker.wins:
            if r < gh.left() or l > gh.right(): continue          # window lives on another monitor
            hg = fy - t
            if h != self.support and 30 < hg < 560 * s and r - l > 140:
                tx = min(max(fx, l + 60), r - 60)
                tx = self.A.clamp_x(tx, self.W)
                if abs(tx - fx) < 520 * s: cand.append((t, h, tx))
        if not cand: return False
        t, h, tx = random.choice(cand); hg = fy - t
        vy = -math.sqrt(2 * self.G * (hg + 70 * s)); tt = (-vy + math.sqrt(vy * vy - 2 * self.G * hg)) / self.G
        self.vx, self.vy = (tx - fx) / tt, vy
        self.walk_dir = 1 if tx >= fx else -1
        self.grounded = False; self.support = None; self.air_kind = "jump"; self.bounces = 0
        self.enter("fall"); self.say(self.line(JUMP_LINES), 1.6)
        return True

    # ---------------- per-frame logic ----------------
    def tick(self, dt):
        self.frame_n += 1; self.dt = dt
        cur = QtGui.QCursor.pos(); now = time.time()
        self.cursor_logic(cur, dt, now)
        if self.dragging: pass
        elif not self.grounded: self.air(dt)
        else: self.behave(dt, cur)
        self.rig.advance(dt)
        now = time.time()
        self.gaze_logic(dt, cur, now)
        self.chat_logic(dt, now)
        self.chat_t -= dt
        if self.chat_t <= 0:
            self.chat_t = random.uniform(30, 80)
            if self.grounded and not self.bubble and self.state == "idle" and not self.in_chat(now):
                self.say(self.line(IDLE_LINES))
        if self.bubble and now - self.bubble[1] > self.bubble[2]: self.bubble = None; self.mask_dirty = True
        if self.spin_t >= 0:
            self.spin_t += dt
            if self.spin_t > 1.1: self.spin_t = -1.0
        for q in self.parts:
            q["life"] += dt; q["x"] += q["vx"] * dt; q["y"] += q["vy"] * dt
            q["vx"] *= 0.985; q["vy"] = q["vy"] * 0.985 - (30 if q["kind"] != "puff" else -10) * dt
        self.parts = [q for q in self.parts if q["life"] < q["max"]]
        self.procedural(dt, cur)
        self.render_frame(cur)

    # ---------------- pet-to-pet per-frame logic ----------------
    def gaze_logic(self, dt, cur, now):
        """Glance at another character now and then - but never fight the cursor."""
        cfg = self.cfg
        if not cfg["peer_look"]:
            self.gaze_peer = None; self.gaze_left = 0.0; return
        # the user always wins: a nearby cursor overrides peer-gazing
        hx, hy = self.px + self.head_px[0], self.py + self.head_px[1]
        cur_close = math.hypot(cur.x() - hx, cur.y() - hy) < self.W * 1.2
        if self.dragging or self.state == "shot" or self.state == "fall" or cur_close:
            self.gaze_peer = None; self.gaze_left = 0.0; self.gaze_cd = random.uniform(3.0, 9.0); return
        if self.gaze_left > 0:
            self.gaze_left -= dt
            if self.gaze_peer is not None and (not self.gaze_peer.isVisible()
                                               or abs(self.gaze_peer.feet_pos()[0] - self.feet_pos()[0]) > self.W * 4.0):
                self.gaze_peer = None
            if self.gaze_left <= 0: self.gaze_peer = None
            return
        self.gaze_cd -= dt
        if self.gaze_cd > 0 or not self.grounded or self.state not in ("idle", "alert", "walk"): return
        self.gaze_cd = random.uniform(4.0, 12.0) / max(0.05, cfg["peer_look_rate"])
        if random.random() > cfg["peer_look_rate"]: return
        ps = [q for q in self.peers(max_dist=self.W * 2.4, same_surface=True) if q.grounded]
        if ps: self.gaze_peer = ps[0]; self.gaze_left = random.uniform(2.5, 6.0)

    def chat_logic(self, dt, now):
        """Continue an ongoing conversation, or let one start."""
        cfg = self.cfg
        if self.chat_lines is not None:
            # a chat survives a bubble or a wander tick, but not a drag / fall / cut-in
            if self.dragging or not self.grounded or self.state == "shot":
                self.chat_abort(); return
            self.chat_step(now)
            return
        if not cfg["chat"]: return
        if self.meet_target is not None:
            self.meet_logic(now); return
        # roll for a spontaneous conversation
        if not cfg["chat_rate"] or not self.grounded or self.dragging or self.state != "idle": return
        if self.bubble is not None: return
        if random.random() > dt * 0.012 * cfg["chat_rate"]: return
        for q in self.peers(max_dist=self.W * 3.0):
            if q.free() and not q.busy_chat():
                # optionally walk over before talking
                if cfg["chat_meetup"] and abs(q.feet_pos()[0] - self.feet_pos()[0]) > (self.W + q.W) * 0.5:
                    self.meet_target = q; self.enter("walk", dur=8.0); return
                self.start_chat(q); return

    def meet_logic(self, now):
        """The 'walk over to talk' step: approach, then begin."""
        other = self.meet_target
        if other is None or not other.isVisible() or not self.grounded or self.dragging:
            self.meet_target = None; return
        gap = abs(other.feet_pos()[0] - self.feet_pos()[0])
        if gap < max(1.0, (self.W + other.W) * 0.45):
            self.meet_target = None
            if self.state == "walk": self.enter("idle"); self.plan_next()
            self.start_chat(other); return
        if self.state != "walk": self.enter("walk", dur=8.0)
        self.walk_dir = 1 if other.feet_pos()[0] > self.feet_pos()[0] else -1
        self.gaze_peer = other; self.gaze_left = 0.4

    def start_chat(self, other):
        """Begin a conversation with `other` immediately (no approach step)."""
        if other is None or other is self: return False
        if self.busy_chat() or other.busy_chat(): return False
        if not (self.free() and other.free()): return False
        topic = self.chat_pick_topic(other)
        lines = list(CHAT_TOPICS[topic])
        cool = time.time() + random.uniform(90, 240)
        self.avoid[topic] = cool; other.avoid[topic] = cool
        self.last_topic = other.last_topic = topic
        for p in (self, other):
            p.chat_lines = lines; p.chat_i = 0
            p.chat_next = time.time() + random.uniform(0.2, 0.7)
            p.chat_with = other if p is self else self
            p.chat_lead = p is self                     # exactly one pet drives the turns
            p.chat_topic = topic
        # face each other for the whole conversation
        self.gaze_peer = other; self.gaze_left = 99.0
        other.gaze_peer = self; other.gaze_left = 99.0
        return True

    def chat_stop(self):
        """User-driven stop (menu / panel)."""
        self.chat_abort()

    # ---------------- input ----------------
    def cursor_logic(self, cur, dt, now):
        lx, ly = cur.x() - self.px, cur.y() - self.py
        over = False
        if self.alpha is not None and 0 <= lx < self.W and 0 <= ly < self.H:
            h, w = self.alpha.shape
            over = self.alpha[min(h - 1, int(ly * h / self.H)), min(w - 1, int(lx * w / self.W))] > 16
        self.hover = over and not self.dragging
        if sys.platform == "win32" and self.cfg["click_through"] and not self.A.args.no_mask:
            bg = self.bubble_geom()
            on_bubble = bg is not None and bg[0].contains(QPointF(lx, ly))
            self.set_passthrough(not (over or on_bubble or self.dragging or self.press_g is not None))
        else:
            self.set_passthrough(False)
        if self.hover: self.hover_time += dt
        else: self.hover_time = 0.0; self.hover_said = False
        if self.hover and self.hover_time > 3.5 and not self.hover_said and self.grounded and not self.bubble:
            self.hover_said = True; self.say(self.line(HOVER_LINES))
        # head-pat: wiggle the cursor horizontally over the head
        hx, hy = self.head_px; r = self.H * 0.11
        inside = (lx - hx) ** 2 + (ly - hy) ** 2 < r * r and not self.dragging and self.press_g is None
        if inside:
            self.gaze_peer = None; self.gaze_left = 0.0     # eyes on the user, not a friend
            dxm = cur.x() - self.last_cur.x()
            if abs(dxm) > 2:
                s = 1 if dxm > 0 else -1
                if self.pat_sign != 0 and s != self.pat_sign: self.pat_events.append(now)
                self.pat_sign = s
            self.pat_events = [t for t in self.pat_events if now - t < 1.4]
            if len(self.pat_events) >= 5 and now > self.pat_cool:
                self.pat_cool = now + 4.0; self.pat_events = []; self.headpat()
            if len(self.pat_events) >= 2: self.bow = min(1.0, self.bow + dt * 2.0)
        else:
            self.pat_events = []; self.pat_sign = 0
        self.bow = max(0.0, self.bow - dt * 0.8)
        self.last_cur = QPoint(cur)
        if not self.dragging:
            self.setCursor(Qt.CursorShape.OpenHandCursor if self.hover else Qt.CursorShape.ArrowCursor)

    def behave(self, dt, cur):
        A, cfg = self.A, self.cfg
        fx, fy = self.feet_pos()
        if self.support is not None:
            gh = A.resolve_screen()
            r = A.tracker.rect(self.support)
            if (r is None or not (r[0] + 4 <= fx <= r[2] - 4)
                    or not (gh.left() + 4 <= fx <= gh.right() - 4)):   # window closed / slid to another monitor
                self.support = None; self.grounded = False; self.vx = self.vy = 0.0
                self.air_kind = "jump"; self.bounces = 0; self.say(self.line(LOST_LINES), 1.8)
                self.enter("fall"); return
            fy = r[1]
        else:
            fy = A.floor_y(fx)
        self.set_feet(fx, fy)
        self.state_t += dt; st = self.state
        if st == "shot":
            if self.rig.done:
                self.enter("idle"); self.plan_next()
            return
        if cfg["follow_cursor"] and st in ("idle", "walk", "alert"):
            dx = cur.x() - fx
            if abs(dx) > 120 * cfg["scale"]:
                self.walk_dir = 1 if dx > 0 else -1
                if st != "walk": self.enter("walk", dur=999.0)
                st = "walk"
            elif st == "walk": self.enter("idle"); st = "idle"
        if st == "idle":
            self.plan_t -= dt
            if self.plan_t <= 0:
                # don't wander off mid-conversation
                if self.busy_chat(): self.plan_t = 1.5
                elif cfg["wander"] and not cfg["follow_cursor"]: self.plan_next_action()
                else: self.plan_t = 3.0
        elif st == "alert":
            if self.state_t > self.state_dur: self.enter("idle"); self.plan_next()
        elif st == "walk":
            self.walk(dt); self.pass_greet(dt)

    def plan_next_action(self):
        r = random.random(); fx, _ = self.feet_pos()
        if self.cfg["perch"] and self.A.tracker.enabled and r < 0.18 and self.try_jump(): return
        if r < 0.58:
            g = self.A.resolve_screen(); L, R = g.left(), g.right()
            self.walk_dir = random.choice([-1, 1]) if L + 300 < fx < R - 300 else (1 if fx < (L + R) / 2 else -1)
            self.enter("walk", dur=random.uniform(2.5, 6.0))
        elif r < 0.78: self.enter("alert", dur=random.uniform(3, 5.5))
        else: self.plan_t = random.uniform(2.5, 6.0)

    def walk(self, dt):
        A, cfg = self.A, self.cfg
        fx, fy = self.feet_pos()
        fx += self.walk_dir * cfg["walk_speed"] * cfg["scale"] * dt
        gh = A.resolve_screen(); m = self.W * 0.18
        lo, hi = gh.left() + m, max(gh.left() + m, gh.right() - m)
        if fx < lo: fx = lo; self.walk_dir = 1
        elif fx > hi: fx = hi; self.walk_dir = -1
        if self.support is not None:
            r = A.tracker.rect(self.support)
            if r:
                at_edge = (fx < r[0] + 16 and self.walk_dir < 0) or (fx > r[2] - 16 and self.walk_dir > 0)
                if at_edge:
                    if not self.edge_decided: self.edge_decided = True; self.step_off = random.random() < 0.2
                    if not self.step_off: self.walk_dir *= -1
                else: self.edge_decided = False
        self.set_feet(fx, fy)
        if self.state_t > self.state_dur: self.enter("idle"); self.plan_next()

    # proximity greet: two characters brushing past each other say hello
    def pass_greet(self, dt):
        cfg = self.cfg
        if not cfg["chat"] or not cfg["chat_rate"] or self.dragging: return
        if self.state != "walk" or not self.grounded: return
        if self.busy_chat() or self.meet_target is not None: return
        if random.random() > dt * 0.12 * cfg["chat_rate"]: return
        for q in self.peers(max_dist=self.W * 0.8):
            if q.busy_chat() or q.dragging or not q.isVisible(): continue
            if abs(q.feet_pos()[1] - self.feet_pos()[1]) > self.H * 0.35: continue   # not on the same level
            self.greet(); return

    def air(self, dt):
        A, cfg = self.A, self.cfg
        fx, fy = self.feet_pos()
        self.vy += self.G * dt
        nfx, nfy = fx + self.vx * dt, fy + self.vy * dt
        gh = A.resolve_screen(); m = self.W * 0.15
        lo, hi = gh.left() + m, max(gh.left() + m, gh.right() - m)
        if nfx < lo: nfx = lo; self.vx = abs(self.vx) * 0.55
        elif nfx > hi: nfx = hi; self.vx = -abs(self.vx) * 0.55
        lim = gh.top() - self.H * 0.4
        if nfy < lim: nfy = lim; self.vy = abs(self.vy) * 0.4
        landed = None
        if self.vy >= 0:
            for top, h in A.surfaces(nfx, cfg["perch"]):
                if (h is None and nfy >= top) or (fy <= top + 4 and nfy >= top): landed = (top, h); break
        if landed: self.land(nfx, landed)
        else: self.set_feet(nfx, nfy)

    def land(self, fx, surf):
        top, h = surf; impact = self.vy
        self.set_feet(fx, top)
        if impact > 1000 and self.bounces < 1:
            self.bounces += 1; self.vy = -impact * 0.28; self.vx *= 0.6; self.sq = min(0.22, impact / 7000); return
        self.bounces = 0; self.grounded = True; self.support = h; self.vx = self.vy = 0.0
        self.sq = min(0.26, impact / 6500)
        self.emit("puff", 6, self.W / 2, self.H * (1 - self.PADB), spread=90)
        if self.air_kind == "thrown" and impact > 900:
            self.say(self.line(LAND_LINES)); self.enter("shot", "react")
        else:
            self.enter("idle"); self.plan_t = 1.5
            if h is not None and random.random() < 0.5: self.say(self.line(PERCH_LINES), 2.0)

    def procedural(self, dt, cur):
        cfg = self.cfg
        hx, hy = self.px + self.head_px[0], self.py + self.head_px[1]
        # look target: another character if one was picked, else the cursor
        tx_, ty_ = cur.x(), cur.y(); dist = None
        peer = self.gaze_peer
        if peer is not None and peer.isVisible() and self.gaze_left > 0:
            tx_ = peer.px + peer.head_px[0]; ty_ = peer.py + peer.head_px[1]
            dist = math.hypot(tx_ - hx, ty_ - hy)
        depth = max(self.H * 1.4, dist) if dist is not None else self.H * 1.4
        ax = math.atan2(tx_ - hx, depth); ay = math.atan2(-(ty_ - hy), depth)
        active = ((cfg["look_at_cursor"] and self.gaze_peer is None)
                  or self.gaze_peer is not None) and self.state in ("idle", "alert", "shot", "walk")
        k = 0.25 if self.state == "walk" else (0.3 if self.shot_key == "cutin" and self.state == "shot" else 1.0)
        tx, ty = (ax * k, ay * k) if active else (0.0, 0.0)
        f = 1 - math.exp(-dt * 7)
        self.gx += (tx - self.gx) * f; self.gy += (ty - self.gy) * f
        if self.state == "walk" or (not self.grounded and self.air_kind == "jump" and not self.dragging):
            by = self.walk_dir * 1.35 if self.state == "walk" else self.walk_dir * 0.9
        elif self.gaze_peer is not None:
            # turn the shoulders toward whoever she is watching, but never past ~45 deg
            by = clamp(self.gx * 0.8, -0.75, 0.75)
        else:
            by = clamp(self.gx * 0.5, -0.55, 0.55)
        self.body_yaw += (by - self.body_yaw) * (1 - math.exp(-dt * 6))
        if self.state == "walk": self.head_yaw = clamp(-self.body_yaw * 0.35 + self.gx, -1.0, 1.0)
        else: self.head_yaw = clamp(self.gx - self.body_yaw, -1.0, 1.0)
        self.head_pitch = clamp(self.gy, -0.5, 0.55) - self.bow * 0.35
        # pendulum roll while carried / tumbling
        vxd = 0.0
        if self.dragging: vxd = (self.px - self.last_px) / max(dt, 1e-3)
        elif not self.grounded and self.air_kind == "thrown": vxd = self.vx * 0.6
        self.last_px = self.px
        rt = clamp(-vxd * 0.0006, -0.5, 0.5)
        self.roll_v += (60 * (rt - self.roll) - 9 * self.roll_v) * dt; self.roll += self.roll_v * dt
        self.sq_v += (-180 * self.sq - 14 * self.sq_v) * dt; self.sq += self.sq_v * dt
        zt = 1.08 if (self.state == "shot" and self.shot_key == "cutin") else 1.0
        self.zoom += (zt - self.zoom) * (1 - math.exp(-dt * 8))

    def render_frame(self, cur):
        A, cfg, R = self.A, self.cfg, self.ch.renderer
        world = self.rig.evaluate(self.head_yaw, self.head_pitch)
        yaw = self.model.face_yaw + math.radians(cfg["yaw_offset"]) + self.body_yaw
        if self.spin_t >= 0:
            p = clamp(self.spin_t / 1.1, 0, 1); yaw += math.tau * (p * p * (3 - 2 * p))
        f = R.fit
        feet = np.array([f["cx"], f["miny"], f["cz"]]); top = np.array([f["cx"], f["maxy"], f["cz"]])
        z = self.zoom
        B = translate(feet) @ scale4((1 + 0.5 * self.sq) * z, (1 - self.sq) * z, (1 + 0.5 * self.sq) * z) @ m4(rot_y(yaw)) @ translate(-feet)
        M = (translate(top) @ rot_z4(self.roll) @ translate(-top)) @ B
        # light: cursor acts as a point light hovering in front of the screen
        if cfg["light_follow"]:
            cx, cy = self.px + self.W / 2, self.py + self.H * 0.4
            sw, sh = A.screen_size()
            tgt = np.array([clamp((cur.x() - cx) / (sw * 0.5) * 1.6, -2, 2), clamp(-(cur.y() - cy) / (sh * 0.5) * 1.6, -2, 2), 0.9])
        else:
            az, el = math.radians(cfg["light_az"]), math.radians(cfg["light_el"])
            tgt = np.array([math.cos(el) * math.sin(az), math.sin(el), math.cos(el) * math.cos(az)])
        self.light += (tgt - self.light) * min(1.0, 1.0 - math.exp(-self.dt * 12))     # B7: frame-rate independent
        raw = R.draw(world, M, self.light, cfg)
        w, h = R.w, R.h
        self.raw = raw
        self.img = QtGui.QImage(raw, w, h, w * 4, QtGui.QImage.Format.Format_RGBA8888_Premultiplied).mirrored(False, True)
        self.img.setDevicePixelRatio(self.dpr)              # B8: no resample on fractional-DPI screens
        self.alpha = np.frombuffer(raw, np.uint8).reshape(h, w, 4)[::-1, :, 3]
        if self.model.head is not None:
            hxp, hyp = R.project(world[self.model.head][:3, 3], M)
            self.head_px = (hxp / self.dpr * (self.W * self.dpr / w), hyp / self.dpr * (self.H * self.dpr / h) - 0.05 * self.H)
        if cfg["click_through"] and not A.args.no_mask:
            if sys.platform == "win32":
                if self.mask_key is not None: self.clearMask(); self.mask_key = None
            elif self.frame_n % 3 == 0 or self.mask_dirty: self.update_mask()
        elif self.mask_key is not None:
            self.clearMask(); self.mask_key = None
        self.update()

    # ---------------- click-through mask ----------------
    def bubble_geom(self):
        if not self.bubble: return None
        fm = QtGui.QFontMetrics(self.bfont)
        r = fm.boundingRect(0, 0, int(self.W * 0.82), 2000, TWRAP, self.bubble[0])
        w, h = r.width() + 28, r.height() + 20
        bx = (self.W - w) / 2; by = max(4.0, self.H * (1 - self.PADB - Renderer.FRAC) - h - 14)
        return QRectF(bx, by, w, h), r

    def update_mask(self):
        self.mask_dirty = False
        a = self.alpha
        if a is None: return
        h, w = a.shape
        ys = np.minimum((np.arange(self.H) * h / self.H).astype(int), h - 1)
        xs = np.minimum((np.arange(self.W) * w / self.W).astype(int), w - 1)
        m = a[np.ix_(ys, xs)] > 12
        def dil(x):
            d = x.copy(); d[:, 1:] |= x[:, :-1]; d[:, :-1] |= x[:, 1:]; d[1:] |= x[:-1]; d[:-1] |= x[1:]; return d
        m = dil(dil(m))
        packed = np.packbits(m, axis=1, bitorder="little").tobytes()
        bg = self.bubble_geom()
        key = (hash(packed), self.grounded, None if bg is None else (int(bg[0].x()), int(bg[0].y()), int(bg[0].width()), int(bg[0].height())),
               tuple((int(q["x"]), int(q["y"]), q["kind"]) for q in self.parts))
        if key == self.mask_key: return
        self.mask_key = key
        try:
            bm = QtGui.QBitmap.fromData(QSize(self.W, self.H), packed, QtGui.QImage.Format.Format_MonoLSB)
            region = QtGui.QRegion(bm)
            if self.grounded:
                rw = self.W * 0.17; cy = self.H * (1 - self.PADB)
                region = region.united(QtGui.QRegion(QtCore.QRect(int(self.W / 2 - rw), int(cy - rw * 0.24), int(rw * 2), int(rw * 0.48)),
                                                     QtGui.QRegion.RegionType.Ellipse))
            if bg is not None: region = region.united(QtGui.QRegion(bg[0].toAlignedRect().adjusted(-2, -2, 2, 14)))
            # A3: particles float outside the silhouette, so fold them into the mask too
            for q in self.parts:
                r = int(q["size"] * 2.2) + 2
                region = region.united(QtGui.QRegion(QtCore.QRect(int(q["x"] - r), int(q["y"] - r), r * 2, r * 2),
                                                         QtGui.QRegion.RegionType.Ellipse))
            self.setMask(region)
        except Exception as e:
            print("[warn] mask disabled:", e); self.cfg["click_through"] = False

    # ---------------- painting ----------------
    def paintEvent(self, _e):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QtGui.QPainter.RenderHint.SmoothPixmapTransform)
        p.setCompositionMode(QtGui.QPainter.CompositionMode.CompositionMode_Source)
        p.fillRect(self.rect(), QtGui.QColor(0, 0, 0, 0))
        p.setCompositionMode(QtGui.QPainter.CompositionMode.CompositionMode_SourceOver)
        if self.grounded:                                           # contact shadow
            rw = self.W * 0.21; cy = self.H * (1 - self.PADB)
            p.save(); p.translate(self.W / 2, cy); p.scale(1, 0.22)
            g = QtGui.QRadialGradient(0, 0, rw); g.setColorAt(0, QtGui.QColor(10, 20, 50, 95)); g.setColorAt(1, QtGui.QColor(10, 20, 50, 0))
            p.setPen(Qt.PenStyle.NoPen); p.setBrush(QtGui.QBrush(g)); p.drawEllipse(QPointF(0, 0), rw, rw); p.restore()
        if self.img is not None: p.drawImage(QRectF(0, 0, self.W, self.H), self.img)
        for q in self.parts:
            al = max(0.0, 1 - (q["life"] / q["max"]) ** 2)
            p.save(); p.translate(q["x"], q["y"]); p.rotate(q["rot"] * q["life"] * 90)
            if q["kind"] == "heart":
                p.setPen(Qt.PenStyle.NoPen); p.setBrush(QtGui.QColor(255, 110, 160, int(235 * al))); p.drawPath(heart_path(q["size"]))
            elif q["kind"] == "star":
                p.setPen(Qt.PenStyle.NoPen); p.setBrush(QtGui.QColor(140, 210, 255, int(240 * al))); p.drawPath(star_path(q["size"] * 1.2))
            else:
                p.setPen(Qt.PenStyle.NoPen); p.setBrush(QtGui.QColor(255, 255, 255, int(110 * al)))
                p.drawEllipse(QPointF(0, 0), q["size"] * (1 + q["life"]), q["size"] * (1 + q["life"]) * 0.7)
            p.restore()
        bg = self.bubble_geom()
        if bg is not None:
            rect, tr = bg; text, t0, dur = self.bubble
            age = time.time() - t0
            al = clamp(min(age / 0.15, (dur - age) / 0.3), 0, 1)
            p.save(); p.setOpacity(al)
            path = QtGui.QPainterPath(); path.addRoundedRect(rect, 14, 14)
            tail = QtGui.QPainterPath()
            cx = self.W / 2
            tail.addPolygon(QtGui.QPolygonF([QPointF(cx - 9, rect.bottom() - 1), QPointF(cx + 9, rect.bottom() - 1), QPointF(cx, rect.bottom() + 11)]))
            path = path.united(tail)
            p.setPen(QtGui.QPen(QtGui.QColor("#7cbcff"), 2)); p.setBrush(QtGui.QColor(255, 255, 255, 246)); p.drawPath(path)
            p.fillRect(QRectF(rect.x() + 12, rect.y() + 5, rect.width() - 24, 3), QtGui.QColor("#128cff"))
            p.setFont(self.bfont); p.setPen(QtGui.QColor("#18325a"))
            p.drawText(QRectF(rect.x() + 14, rect.y() + 12, tr.width() + 2, tr.height() + 2), TWRAP, text)
            p.restore()
        p.end()

    # ---------------- mouse ----------------
    def mousePressEvent(self, e):
        self.A.set_active(self)
        if e.button() in (Qt.MouseButton.LeftButton, Qt.MouseButton.MiddleButton):
            self.press_g = e.globalPosition().toPoint(); self.press_win = QPoint(int(self.px), int(self.py))
            mods = e.modifiers()
            self.rotating = e.button() == Qt.MouseButton.MiddleButton or bool(mods & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier))
            self.rot_yaw0 = self.cfg["yaw_offset"]; self.hist.clear(); self.moved = False
            self.bubble = None if (self.bubble and self.bubble_geom() and self.bubble_geom()[0].contains(e.position())) else self.bubble

    def mouseMoveEvent(self, e):
        if self.press_g is None: return
        g = e.globalPosition().toPoint(); d = g - self.press_g
        if self.rotating:
            self.moved = True
            self.A.set("yaw_offset", ((self.rot_yaw0 + d.x() * 0.7 + 180) % 360) - 180, refresh=True)
            return
        if not self.dragging and d.manhattanLength() > 6:
            self.dragging = True; self.moved = True; self.grounded = False; self.support = None
            self.chat_abort(); self.meet_target = None
            self.vx = self.vy = 0.0; self.bounces = 0; self.air_kind = "thrown"
            self.enter("drag"); self.setCursor(Qt.CursorShape.ClosedHandCursor)
            if random.random() < 0.6: self.say(self.line(DRAG_LINES), 2.0)
            self.audience("drag")
        if self.dragging:
            nx = self.press_win.x() + d.x(); ny = self.press_win.y() + d.y()
            # keep her inside the one screen, horizontally
            g_home = self.A.resolve_screen()
            fx = clamp(nx + self.W / 2, g_home.left() + self.W * 0.10, max(g_home.left() + self.W * 0.10,
                                                                              g_home.right() - self.W * 0.90))
            nx = fx - self.W / 2
            ny = clamp(ny, g_home.top() - self.H * 0.5, g_home.bottom() - self.H * 0.15)
            self.px = nx; self.py = ny
            self.move(int(self.px), int(self.py)); self.hist.append((time.time(), g.x(), g.y()))

    def mouseReleaseEvent(self, e):
        if self.press_g is None: return
        was_drag, rot, moved = self.dragging, self.rotating, getattr(self, "moved", False)
        self.press_g = None; self.rotating = False
        if was_drag:
            self.dragging = False; self.setCursor(Qt.CursorShape.ArrowCursor)
            vx = vy = 0.0; now = time.time()
            pts = [h for h in self.hist if now - h[0] < 0.14]
            if len(pts) >= 2 and pts[-1][0] > pts[0][0]:
                dt = pts[-1][0] - pts[0][0]
                vx, vy = (pts[-1][1] - pts[0][1]) / dt, (pts[-1][2] - pts[0][2]) / dt
            sp = math.hypot(vx, vy)
            if sp < 150: vx = vy = 0.0
            elif sp > 3000: vx, vy = vx * 3000 / sp, vy * 3000 / sp
            self.vx, self.vy = vx, vy; self.air_kind = "thrown"; self.bounces = 0
            self.enter("fall")
        elif not rot and not moved:
            self.poke()

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton: self.do_cutin()

    def wheelEvent(self, e):
        dy = e.angleDelta().y() / 120.0
        mods = e.modifiers()
        if mods & Qt.KeyboardModifier.ControlModifier:
            self.A.set("yaw_offset", ((self.cfg["yaw_offset"] + dy * 15 + 180) % 360) - 180, refresh=True)  # this one only
        elif mods & Qt.KeyboardModifier.ShiftModifier:
            self.A.set("yaw_offset", ((self.cfg["yaw_offset"] + dy * 15 + 180) % 360) - 180, refresh=True)  # everyone
        else:
            self.A.set("scale", clamp(self.cfg["scale"] * (1.06 ** dy), 0.5, 2.5), refresh=True)

    def contextMenuEvent(self, e):
        self.A.set_active(self)
        self.A.show_menu(e.globalPos(), self)


# =============================================================================
#  Control panel (tabs)
# =============================================================================
QSS = """
QWidget { font-family: 'Segoe UI','Noto Sans',sans-serif; font-size: 12px; color:#1d2b45; }
QWidget#root { background:#eef4fb; }
QTabWidget::pane { border:1px solid #c9daf0; border-radius:8px; background:white; top:-1px; }
QTabBar::tab { padding:7px 18px; background:#dbe8f7; border-top-left-radius:8px; border-top-right-radius:8px; margin-right:2px; color:#3a5a86; }
QTabBar::tab:selected { background:#128cff; color:white; font-weight:600; }
QPushButton { background:#128cff; color:white; border:none; border-radius:8px; padding:7px 12px; font-weight:600; }
QPushButton:hover { background:#3aa1ff; } QPushButton:pressed { background:#0b6fd0; }
QPushButton#alt { background:#dbe8f7; color:#1d4e89; } QPushButton#alt:hover { background:#c7ddf5; }
QSlider::groove:horizontal { height:6px; background:#d6e4f5; border-radius:3px; }
QSlider::sub-page:horizontal { background:#128cff; border-radius:3px; }
QSlider::handle:horizontal { background:white; border:2px solid #128cff; width:14px; margin:-6px 0; border-radius:8px; }
QComboBox { border:1px solid #bcd3ee; border-radius:6px; padding:4px 8px; background:white; }
QMenu { background:white; border:1px solid #bcd3ee; padding:4px; }
QMenu::item { padding:6px 24px; border-radius:4px; } QMenu::item:selected { background:#128cff; color:white; }
QMenu::separator { height:1px; background:#dbe8f7; margin:4px 8px; }
QLabel#title { font-size:16px; font-weight:700; color:white; }
QLabel#status { font-family:Consolas,monospace; background:#f2f7fd; border:1px solid #d3e3f6; border-radius:6px; padding:6px; }
"""

class Panel(QtWidgets.QWidget):
    def __init__(self, A):
        super().__init__()
        self.A = A; self.ctrl = {}
        self.setObjectName("root"); self.setWindowTitle("Desktop Pets - Control Panel"); self.resize(520, 660)
        lay = QtWidgets.QVBoxLayout(self); lay.setContentsMargins(10, 10, 10, 10)
        head = QtWidgets.QFrame(); head.setStyleSheet("background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #0b4fb3,stop:1 #35b6ff); border-radius:10px;")
        hl = QtWidgets.QHBoxLayout(head)
        self.title = QtWidgets.QLabel("Desktop Pets"); self.title.setObjectName("title"); hl.addWidget(self.title)
        lay.addWidget(head)
        tabs = QtWidgets.QTabWidget(); lay.addWidget(tabs, 1)
        tabs.addTab(self.tab_chars(), "Characters"); tabs.addTab(self.tab_pet(), "Pet")
        tabs.addTab(self.tab_shader(), "Shader"); tabs.addTab(self.tab_interact(), "Interact")
        tabs.addTab(self.tab_help(), "Help")
        self.status = QtWidgets.QLabel(""); self.status.setObjectName("status"); lay.addWidget(self.status)

    # ---- widgets helpers ----
    def check(self, lay, label, key):
        cb = QtWidgets.QCheckBox(label); cb.setChecked(bool(self.A.cfg[key]))
        cb.toggled.connect(lambda v, k=key: self.A.set(k, v)); lay.addWidget(cb); self.ctrl[key] = ("check", cb)

    def slider(self, lay, label, key, lo, hi, mult, fmt="{:.2f}"):
        row = QtWidgets.QHBoxLayout(); lb = QtWidgets.QLabel(label); lb.setMinimumWidth(135)
        sl = QtWidgets.QSlider(Qt.Orientation.Horizontal); sl.setRange(int(lo * mult), int(hi * mult))
        sl.setValue(int(self.A.cfg[key] * mult)); vl = QtWidgets.QLabel(fmt.format(self.A.cfg[key])); vl.setMinimumWidth(46)
        def ch(v, k=key, m=mult, vl=vl, fmt=fmt): vl.setText(fmt.format(v / m)); self.A.set(k, v / m)
        sl.valueChanged.connect(ch)
        row.addWidget(lb); row.addWidget(sl, 1); row.addWidget(vl); lay.addLayout(row)
        self.ctrl[key] = ("slider", sl, mult, vl, fmt)

    def button(self, text, fn, alt=False):
        b = QtWidgets.QPushButton(text); b.clicked.connect(fn)
        if alt: b.setObjectName("alt")
        return b

    def refresh(self):
        for key, c in self.ctrl.items():
            v = self.A.cfg[key]
            if c[0] == "check":
                c[1].blockSignals(True); c[1].setChecked(bool(v)); c[1].blockSignals(False)
            else:
                c[1].blockSignals(True); c[1].setValue(int(v * c[2])); c[1].blockSignals(False); c[3].setText(c[4].format(v))
        self.preset_box.blockSignals(True); self.preset_box.setCurrentText(self.A.cfg["preset"]); self.preset_box.blockSignals(False)

    # ---- tabs ----
    def tab_chars(self):
        w = QtWidgets.QWidget(); l = QtWidgets.QVBoxLayout(w)
        row = QtWidgets.QHBoxLayout(); row.addWidget(QtWidgets.QLabel("Screen"))
        self.screen_box = QtWidgets.QComboBox()
        for i, s in enumerate(self.A.qapp.screens()):
            g = s.availableGeometry()
            self.screen_box.addItem(f"{i}: {s.name()}  ({g.width()}x{g.height()})", i)
        self.screen_box.addItem("Cursor screen at startup", "cursor")
        self.screen_box.addItem("Primary screen", "primary")
        cur = self.A.cfg.get("screen", "cursor")
        idx = self.screen_box.findData(cur)
        self.screen_box.setCurrentIndex(idx if idx >= 0 else self.screen_box.count() - 2)
        self.screen_box.currentIndexChanged.connect(self.on_screen_changed)
        row.addWidget(self.screen_box, 1)
        bt = self.button("Apply", self.on_screen_apply, True); row.addWidget(bt)
        l.addLayout(row)
        self.screen_note = QtWidgets.QLabel(""); self.screen_note.setWordWrap(True); l.addWidget(self.screen_note)
        self.sync_screen_note()
        l.addSpacing(6)
        row = QtWidgets.QHBoxLayout(); row.addWidget(QtWidgets.QLabel("Act on"))
        self.who_box = QtWidgets.QComboBox()
        self.who_box.addItems(["All characters"] + [p.name for p in self.A.pets])
        self.who_box.currentIndexChanged.connect(lambda _i: self.A.set_target(self.who_box.currentIndex()))
        row.addWidget(self.who_box, 1); l.addLayout(row)
        row = QtWidgets.QHBoxLayout(); row.addWidget(QtWidgets.QLabel("Selected"))
        self.sel_box = QtWidgets.QComboBox()
        self.sel_box.addItems([p.name for p in self.A.pets] or ["-"])
        self.sel_box.currentIndexChanged.connect(lambda i: self.A.set_active_by_index(max(0, i)))
        row.addWidget(self.sel_box, 1); l.addLayout(row)
        l.addSpacing(6)
        self.char_rows = []
        for p in self.A.pets:
            box = QtWidgets.QGroupBox(p.name); g = QtWidgets.QGridLayout(box)
            cbs = QtWidgets.QCheckBox("visible"); cbs.setChecked(p.isVisible())
            cbs.toggled.connect(lambda v, pp=p: self.A.set_pet_visible(pp, v))
            g.addWidget(cbs, 0, 0)
            g.addWidget(self.button("Reset pos", lambda pp=p: pp.reset_position(), True), 0, 1)
            g.addWidget(self.button("Summon", lambda pp=p: self.A.summon(pp), True), 0, 2)
            g.addWidget(self.button("Poke", lambda pp=p: pp.poke(), True), 0, 3)
            g.addWidget(self.button("Say", lambda pp=p: pp.say(pp.line(IDLE_LINES + POKE_LINES)), True), 0, 4)
            g.addWidget(self.button("Spin", lambda pp=p: pp.spin(), True), 0, 5)
            l.addWidget(box)
            self.char_rows.append((p, cbs))
        l.addWidget(self.button("Show all", lambda: self.A.set_all_visible(True)))
        l.addWidget(self.button("Hide all", lambda: self.A.set_all_visible(False)))
        note = QtWidgets.QLabel("Tip: if no characters appear at startup, something was left "
                                "hidden last time. Press <b>Show all</b>.")
        note.setWordWrap(True); l.addWidget(note)
        l.addStretch(1); return w

    def sync_char_rows(self):
        for p, cb in getattr(self, "char_rows", ()):
            cb.blockSignals(True); cb.setChecked(p.isVisible()); cb.blockSignals(False)

    def sync_screen_note(self):
        if getattr(self, "screen_note", None):
            g = self.A.resolve_screen()
            self.screen_note.setText(f"All characters are locked to screen {self.A.screen_label()}  "
                                     f"(x {g.left()}..{g.right()}, y {g.top()}..{g.bottom()}).")

    def on_screen_changed(self, _i): self.sync_screen_note()

    def on_screen_apply(self):
        self.A.set_screen(self.screen_box.currentData())
        self.sync_screen_note()

    def tab_pet(self):
        w = QtWidgets.QWidget(); l = QtWidgets.QVBoxLayout(w)
        self.slider(l, "Size", "scale", 0.5, 2.5, 100)
        self.slider(l, "Walk speed", "walk_speed", 20, 220, 1, "{:.0f}")
        self.slider(l, "Model rotation (deg)", "yaw_offset", -180, 180, 1, "{:.0f}")
        l.addSpacing(6)
        for label, key in (("Wander around on her own", "wander"), ("Look at my cursor", "look_at_cursor"),
                           ("Follow my cursor (walk to it)", "follow_cursor"),
                           ("Perch / jump on top of my windows (Windows only)", "perch"),
                           ("Speech bubbles", "speech"), ("Particles (hearts / sparkles)", "particles"),
                           ("Always on top", "always_on_top"), ("Click-through transparent areas", "click_through")):
            self.check(l, label, key)
        l.addSpacing(6)
        l.addWidget(QtWidgets.QLabel("Together"))
        self.check(l, "Look at other characters", "peer_look")
        self.slider(l, "How often they glance over", "peer_look_rate", 0.0, 1.0, 100, "{:.2f}")
        self.check(l, "Talk to each other", "chat")
        self.slider(l, "Chattiness", "chat_rate", 0.0, 1.0, 100, "{:.2f}")
        self.check(l, "Walk over before talking", "chat_meetup")
        row = QtWidgets.QHBoxLayout(); row.addWidget(QtWidgets.QLabel("Frame rate"))
        self.fps_box = QtWidgets.QComboBox(); self.fps_box.addItems(["30", "60"]); self.fps_box.setCurrentText(str(int(self.A.cfg["fps"])))
        self.fps_box.currentTextChanged.connect(lambda s: self.A.set("fps", int(s))); row.addWidget(self.fps_box); row.addStretch(1); l.addLayout(row)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(self.button("Flip 180°", lambda: self.A.set("yaw_offset", ((self.A.cfg["yaw_offset"] + 180 + 180) % 360) - 180, refresh=True), True))
        row.addWidget(self.button("Reset positions", lambda: [p.reset_position() for p in self.A.pets], True))
        row.addWidget(self.button("Hide to tray", lambda: self.A.toggle_pet(), True)); l.addLayout(row)
        l.addStretch(1); return w

    def tab_shader(self):
        w = QtWidgets.QWidget(); l = QtWidgets.QVBoxLayout(w)
        row = QtWidgets.QHBoxLayout(); row.addWidget(QtWidgets.QLabel("Preset"))
        self.preset_box = QtWidgets.QComboBox(); self.preset_box.addItems(list(PRESETS)); self.preset_box.setCurrentText(self.A.cfg["preset"])
        self.preset_box.currentTextChanged.connect(self.A.apply_preset); row.addWidget(self.preset_box, 1); l.addLayout(row)
        self.slider(l, "Shadow threshold", "toon_threshold", 0.05, 0.95, 100)
        self.slider(l, "Shadow softness", "toon_softness", 0.002, 0.30, 1000, "{:.3f}")
        self.slider(l, "Shadow strength", "shadow_strength", 0.0, 1.0, 100)
        self.slider(l, "Deep shadow band", "deep_shadow", 0.0, 1.0, 100)
        self.slider(l, "Light intensity", "exposure", 0.4, 1.5, 100)
        self.slider(l, "Rim light", "rim", 0.0, 1.5, 100)
        self.slider(l, "Specular tick", "spec", 0.0, 1.0, 100)
        self.slider(l, "Saturation", "saturation", 0.4, 1.7, 100)
        self.slider(l, "Outline width (px)", "outline_px", 0.0, 5.0, 100)
        self.slider(l, "Outline darkness", "outline_dark", 0.0, 1.0, 100)
        self.check(l, "Eyes through bangs (experimental)", "eyes_through_bangs")
        self.check(l, "Light follows my cursor (3D point light)", "light_follow")
        self.slider(l, "Manual light azimuth", "light_az", -180, 180, 1, "{:.0f}")
        self.slider(l, "Manual light elevation", "light_el", -20, 85, 1, "{:.0f}")
        l.addStretch(1); return w

    def tab_interact(self):
        w = QtWidgets.QWidget(); l = QtWidgets.QVBoxLayout(w)
        note = QtWidgets.QLabel("Applies to the target chosen on the Characters tab (All = everyone).")
        note.setWordWrap(True); l.addWidget(note)
        grid = QtWidgets.QGridLayout()
        def run(fn):
            self.A.each(fn)
        items = [("Poke  (Cafe_Reaction)", lambda p: p.poke()), ("Headpat", lambda p: p.headpat()),
                 ("EX Cut-in  (Exs_Cutin)", lambda p: p.do_cutin()), ("Tactical Start", lambda p: p.do_tactical()),
                 ("Spin 360° (3D)", lambda p: p.spin()), ("Launch!", lambda p: p.launch()),
                 ("Cafe_Idle", lambda p: p.play_anim("idle")), ("Cafe_Walk", lambda p: p.play_anim("walk")),
                 ("Formation_Idle", lambda p: p.play_anim("alert")), ("Formation_Pickup", lambda p: p.play_anim("pickup"))]
        for i, (t, fn) in enumerate(items):
            grid.addWidget(self.button(t, lambda f=fn: run(f), alt=i >= 6), i // 2, i % 2)
        grid.addWidget(self.button("Say something", lambda: self.A.each(lambda p: p.say(p.line(IDLE_LINES + POKE_LINES)))), 5, 0)
        grid.addWidget(self.button("Summon to cursor", lambda: self.A.summon()), 5, 1)
        l.addLayout(grid)
        l.addSpacing(8)
        l.addWidget(QtWidgets.QLabel("Together"))
        h = QtWidgets.QHBoxLayout()
        h.addWidget(self.button("Start a conversation", lambda: self.A.start_chat()))
        h.addWidget(self.button("Introduce everyone", self.A.introduce_all))
        l.addLayout(h)
        h2 = QtWidgets.QHBoxLayout()
        h2.addWidget(self.button("Stop all conversations", self.A.end_all_chats, True))
        l.addLayout(h2)
        self.chat_note = QtWidgets.QLabel(""); self.chat_note.setWordWrap(True); l.addWidget(self.chat_note)
        l.addStretch(1); return w

    def tab_help(self):
        tb = QtWidgets.QTextBrowser(); tb.setOpenExternalLinks(True)
        tb.setHtml("""<h3>Characters</h3><p>Every <code>.glb</code> file in this folder becomes its own character with its own
        window, animations, walk cycle and dialogue. Drop another <code>.glb</code> in and restart to add more.</p>
        <h3>One screen only</h3><p>All of them are locked to a single monitor - they spawn on it, walk on it, only jump onto
        windows that sit on it, and can't be dragged off it. Pick which monitor on the Characters tab.</p>
        <h3>Together</h3><p>With more than one character they notice each other:</p><ul>
        <li>They <b>glance at each other</b> between idle animations. Move your cursor near one and she looks at you instead.</li>
        <li>They start <b>conversations</b> on their own - different topics, a few lines each, and whoever is
        listening faces the speaker and reacts. Poke one and a nearby character comments on it.</li>
        <li>When two of them walk past each other they say hello.</li></ul>
        <p>All of it is on the Pet tab (<i>Together</i>) and the Interact tab.</p>
        <h3>Controls</h3><ul>
        <li><b>Move cursor</b> - head and body follow it, the light follows it too.</li>
        <li><b>Click</b> - poke. <b>Double-click</b> - EX cut-in.</li>
        <li><b>Drag</b> - pick up (Formation_Pickup). Release while moving to <b>throw</b>.</li>
        <li><b>Wiggle over the head</b> - headpat.</li>
        <li><b>Wheel</b> - resize everyone. <b>Shift+wheel / Ctrl+wheel / middle-drag / Ctrl+drag</b> - rotate in 3D.</li>
        <li><b>Right-click a character</b> - its own quick menu. Tray icon: show/hide, panel, quit.</li></ul>
        <p><b>Click-through</b> is a per-pixel Windows hit-test; on Linux/macOS it falls back to a mask region.</p>
        <h3>Windows</h3><p>With <i>Perch</i> on they jump up onto your open windows, walk along their title bars,
        ride them while you move them, and fall if you close or minimise them.</p>
        <h3>Tips</h3><p>If a character faces away from you use <b>Flip 180</b>. Run with <code>--dump</code> to print
        bone / animation / material names for every model.</p>""")
        return tb

    def update_status(self):
        p = self.A.active or (self.A.pets[0] if self.A.pets else None)
        if p is None: return
        n = sum(1 for q in self.A.pets if q.isVisible())
        looking = f"looking at {p.gaze_peer.name}" if p.gaze_peer is not None and p.gaze_left > 0 else "looking at cursor"
        chat = ""
        if p.busy_chat():
            if p.meet_target is not None: chat = f"walking over to {p.meet_target.name}"
            elif p.chat_with is not None:
                chat = f"{CHAT_TOPIC_LABELS.get(p.chat_topic, 'chatting')} with {p.chat_with.name}"
        self.status.setText(f"{n} character(s) | screen {self.A.screen_label()} | fps: {self.A.fps:4.0f}\n"
                            f"{p.name}: state {p.state:<6} anim: {ANIM_KEYS[p.anim_key][0]:<17} "
                            f"pos: {int(p.px)},{int(p.py)}  grounded: {p.grounded}  on-window: {p.support is not None}\n"
                            f"{looking}" + (f"  |  {chat}" if chat else ""))
        if getattr(self, "chat_note", None):
            if not self.A.cfg["chat"] or n < 2:
                self.chat_note.setText("Needs 2+ visible characters and <i>Talk to each other</i> on.")
            else:
                self.chat_note.setText(f"Topics: {', '.join(CHAT_TOPIC_LABELS.values())}.")


# =============================================================================
#  Application glue
# =============================================================================
def make_icon(letter="A"):
    pm = QtGui.QPixmap(64, 64); pm.fill(Qt.GlobalColor.transparent)
    p = QtGui.QPainter(pm); p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
    g = QtGui.QLinearGradient(0, 0, 64, 64); g.setColorAt(0, QtGui.QColor("#35b6ff")); g.setColorAt(1, QtGui.QColor("#0b4fb3"))
    p.setBrush(QtGui.QBrush(g)); p.setPen(QtGui.QPen(QtGui.QColor("white"), 3)); p.drawEllipse(4, 4, 56, 56)
    p.setPen(QtGui.QColor("white")); f = QtGui.QFont("Arial", 28, QtGui.QFont.Weight.Bold); p.setFont(f)
    p.drawText(QRectF(0, 0, 64, 62), int(Qt.AlignmentFlag.AlignCenter.value), (letter or "A")[:1].upper()); p.end()
    return QtGui.QIcon(pm)


# =============================================================================
#  One character = one .glb + its own animations, renderer and dialogue
# =============================================================================
class Character:
    def __init__(self, path, index=0, dump=False):
        self.path = Path(path)
        self.index = index
        self.name = self.path.stem.replace("_", " ").replace("-", " ").strip() or f"Pet {index + 1}"
        if len(self.name) > 28: self.name = self.name[:28]
        print(f"Loading {self.path.name} as '{self.name}' ...")
        self.model = Model(self.path)
        if dump: self.model.dump()
        self.clips = {}
        fallback = next(iter(self.model.clips.values()), self.model.rest_clip)
        for key, (name, _loop) in ANIM_KEYS.items():
            c = self.model.find_clip(name)
            if c is None:
                print(f"[warn] {self.path.name}: animation '{name}' not found - using '{fallback.name}'.")
                c = fallback
            self.clips[key] = c
        idle = self.clips["idle"]
        self.fit = self.model.compute_fit(self.model.pose_world(idle.T[0], idle.Q[0], idle.S[0]))
        self.renderer = None            # created lazily (one GL context is enough)


class App(QtCore.QObject):
    def __init__(self, qapp, paths, args):
        super().__init__()
        self.qapp, self.args = qapp, args
        self.cfg = dict(DEFAULTS)
        try:
            if SETTINGS_PATH.exists(): self.cfg.update({k: v for k, v in json.loads(SETTINGS_PATH.read_text()).items() if k in DEFAULTS})
        except Exception: pass
        if not isinstance(self.cfg.get("positions"), dict): self.cfg["positions"] = {}
        if not isinstance(self.cfg.get("hidden"), list): self.cfg["hidden"] = []
        if args.scale: self.cfg["scale"] = args.scale
        if args.fps: self.cfg["fps"] = args.fps
        if getattr(args, "screen", None) is not None: self.cfg["screen"] = args.screen
        self.home = self.resolve_screen()
        print("Locked to screen:", self.screen_label())
        paths = [Path(p) for p in paths]
        print(f"{len(paths)} model(s) found.")
        self.chars = []
        for i, p in enumerate(paths):
            try:
                self.chars.append(Character(p, i, args.dump))
            except Exception as e:
                print(f"[error] could not load {p.name}: {type(e).__name__}: {e}")
                traceback.print_exc()
        if not self.chars:
            raise RuntimeError("none of the .glb files could be loaded")
        self.ctx = moderngl.create_standalone_context(require=330)
        print("GL:", self.ctx.info.get("GL_RENDERER"))
        self.tracker = WinTracker(); self.trk_t = 0.0
        self.cutin = Cutin()
        self.pets = []
        self.active = None
        self.target = -1                                   # -1 = all characters
        hidden = set(self.cfg["hidden"])
        print(f"Saved hidden characters: {len(hidden)} ({', '.join(sorted(hidden)) or 'none'})")
        for i, ch in enumerate(self.chars):
            ch.renderer = Renderer(ch.model, ch.fit, self.ctx)
            pet = Pet(self, ch)
            self.pets.append(pet)
        self.active = self.pets[0]
        self.panel = Panel(self)
        self.setup_tray()
        self.save_timer = QTimer(self); self.save_timer.setSingleShot(True); self.save_timer.timeout.connect(self.save)
        self.last = time.perf_counter(); self.fps = 30.0; self.status_t = 0.0; self.errors = 0
        self.timer = QTimer(self); self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.timeout.connect(self.tick); self.timer.start(int(1000 / self.cfg["fps"]))
        qapp.aboutToQuit.connect(self.save)
        for i, p in enumerate(self.pets):
            p.spawn(i)
        # apply the saved hidden state only once every pet has spawned - doing it
        # inside the loop above judged later pets while they were still invisible
        self.apply_hidden_state(hidden)

    # ---- character selection ----
    def set_active(self, pet):
        if pet is not self.active:
            self.active = pet
            if getattr(self.panel, "sel_box", None):
                i = self.pets.index(pet) if pet in self.pets else -1
                self.panel.sel_box.blockSignals(True); self.panel.sel_box.setCurrentIndex(i); self.panel.sel_box.blockSignals(False)

    def set_active_by_index(self, i):
        if 0 <= i < len(self.pets): self.set_active(self.pets[i])

    def set_target(self, i):
        self.target = i

    def each(self, fn):
        for p in self.targets(): fn(p)

    def targets(self):
        if 0 <= self.target < len(self.pets): return [self.pets[self.target]]
        return list(self.pets)

    # ---- pet-to-pet ----
    def peers_of(self, p, max_dist=None, same_surface=False):
        """Other visible characters near `p`, nearest first.

        max_dist      horizontal reach in px (default ~1.6 character widths)
        same_surface  only characters perched on the same window as `p`
        """
        if max_dist is None: max_dist = BASE_W * 1.6 * self.cfg["scale"]
        fx, fy = p.feet_pos(); out = []
        for q in self.pets:
            if q is p or not q.isVisible(): continue
            qx, qy = q.feet_pos()
            d = abs(qx - fx)
            if d > max_dist: continue
            if same_surface and (q.support is not p.support or q.support is None): continue
            if abs(qy - fy) > p.H * 0.6: continue          # not on the same level
            home = getattr(self, "home", None)
            if home is not None:      # single-screen build: never pair up across monitors
                if qx + q.W * 0.5 < home.left() or qx - q.W * 0.5 > home.right(): continue
            out.append((d + abs(qy - fy) * 0.5, q))
        out.sort(key=lambda t: t[0])
        return [q for _k, q in out]

    def start_chat(self, a=None, b=None):
        """Kick off a conversation, either between two given pets or a free pair."""
        if not self.cfg["chat"]: return None
        if a is None:
            cand = [p for p in self.pets if p.isVisible() and p.free() and not p.busy_chat()]
            if len(cand) < 2: return None
            a = random.choice(cand)
        if b is None:
            ps = [q for q in self.peers_of(a) if q.free() and not q.busy_chat()]
            if not ps: return None
            b = ps[0]
        if a is b or not (a.free() and b.free()): return None
        if a.busy_chat() or b.busy_chat(): return None      # nobody is in two chats
        # "walk over to talk": send the further one over first
        if self.cfg["chat_meetup"] and abs(a.feet_pos()[0] - b.feet_pos()[0]) > (a.W + b.W) * 0.5:
            near, far = (a, b) if a.feet_pos()[0] < b.feet_pos()[0] else (b, a)
            far.meet_target = near
            if far.state not in ("walk", "shot") and not far.dragging: far.enter("walk", dur=8.0)
            return (near, far)
        return (a, b) if a.start_chat(b) else None

    def introduce_all(self):
        """Round-robin greeting: everyone says hi to the next one."""
        live = [p for p in self.pets if p.isVisible() and not p.dragging]
        if len(live) < 2: return
        random.shuffle(live)
        for i, p in enumerate(live):
            q = live[(i + 1) % len(live)]
            if p.free() and q.free():
                p.greet_near(q)

    def end_all_chats(self):
        for p in self.pets: p.chat_abort(); p.meet_target = None

    def set_screen(self, spec):
        """Move every character onto the chosen screen and keep them there."""
        self.cfg["screen"] = spec
        self.resolve_screen(spec)
        self.end_all_chats()                      # nobody mid-conversation when the screen changes
        g = self.home
        print("Locked to screen:", self.screen_label())
        n = len(self.pets) or 1
        for i, p in enumerate(self.pets):
            frac = 0.9 - i * min(0.75 / n, 0.25)
            p.support = None; p.grounded = True; p.vx = p.vy = 0
            p.set_feet(self.clamp_x(g.left() + (g.right() - g.left()) * frac, p.W), g.bottom() + 1)
            p.enter("idle")
        self.save_timer.start(600)
        if hasattr(self, "panel"): self.panel.sync_screen_note()

    def set_pet_visible(self, pet, vis):
        """Show/hide one character.

        Only this character's own name is touched - the list is never rebuilt
        from every pet's isVisible(), which used to mark not-yet-spawned pets
        as hidden and cascade into "everything stays hidden" across restarts.
        """
        if vis: pet.show()
        else: pet.hide()
        names = set(self.cfg.get("hidden") or [])
        if vis: names.discard(pet.name)
        else: names.add(pet.name)
        self.cfg["hidden"] = [p.name for p in self.pets if p.name in names]
        self.save_timer.start(600)
        if getattr(self, "panel", None) and getattr(self.panel, "sync_char_rows", None):
            self.panel.sync_char_rows()

    def set_all_visible(self, vis):
        for p in self.pets: self.set_pet_visible(p, vis)
        if getattr(self.panel, "sync_char_rows", None): self.panel.sync_char_rows()

    def apply_hidden_state(self, hidden):
        """Restore per-character visibility from the saved list (after spawning).

        Two safety nets, both so a bad save can never leave you with a blank screen:
          * stale entries (a character whose .glb is gone) are dropped
          * a list that hides *every* character is the signature of the old
            'Hide to tray' bug, not a real choice, so everybody comes back
        """
        hidden = set(hidden or ())
        known = {p.name for p in self.pets}
        stale = hidden - known
        if known and hidden >= known:
            print("[info] every character was saved as hidden (leftover 'Hide to tray' "
                  "state) - showing them all.")
            hidden = set()
        for p in self.pets:
            p.show() if p.name not in hidden else p.hide()
        self.cfg["hidden"] = sorted(known & hidden)
        if stale:
            print("[info] dropping hidden characters that no longer exist:",
                  ", ".join(sorted(stale)))
        if getattr(self.panel, "sync_char_rows", None): self.panel.sync_char_rows()

    # ---- one screen only: every character is locked to self.home ----
    def resolve_screen(self, spec=None):
        """Pick the single screen the pets live on and cache it as self.home."""
        spec = self.cfg.get("screen", "cursor") if spec is None else spec
        screens = self.qapp.screens()
        home = None
        if isinstance(spec, int) or (isinstance(spec, str) and spec.isdigit()):
            i = int(spec)
            if 0 <= i < len(screens): home = screens[i].availableGeometry()
        if home is None and spec == "primary":
            home = self.qapp.primaryScreen().availableGeometry()
        if home is None:                                   # "cursor" and any bad value
            c = QtGui.QCursor.pos()
            scr = self.qapp.screenAt(c) or self.qapp.primaryScreen()
            home = scr.availableGeometry()
        self.home = home
        return home

    def screen_label(self):
        for i, s in enumerate(self.qapp.screens()):
            if s.availableGeometry() == getattr(self, "home", None):
                return f"{i}: {s.name()} ({s.availableGeometry().width()}x{s.availableGeometry().height()})"
        return "-"

    def bounds(self):
        g = self.resolve_screen()
        return g.left(), g.right() + 1

    def top(self): return self.resolve_screen().top()

    def screen_size(self):
        g = self.resolve_screen(); return g.width(), g.height()

    def floor_y(self, fx):
        return self.resolve_screen().bottom() + 1

    def surfaces(self, fx, perch):
        home = self.resolve_screen()
        res = []
        if perch and self.tracker.enabled:
            for top, h in self.tracker.surfaces(fx):
                if top < home.top() - 4 or top > home.bottom() + 4: continue   # other monitors
                if fx < home.left() + 6 or fx > home.right() - 6: continue
                res.append((top, h))
        res.sort(key=lambda s: s[0]); res.append((self.floor_y(fx), None)); return res

    def clamp_x(self, fx, w):
        """Keep a character's feet inside the home screen."""
        g = self.resolve_screen(); m = w * 0.18
        return clamp(fx, g.left() + m, max(g.left() + m, g.right() - m))

    # ---- settings ----
    def set(self, key, val, refresh=False):
        self.cfg[key] = val; self.save_timer.start(600)
        if key == "scale":
            for p in self.pets: p.apply_scale()
        elif key == "always_on_top":
            for p in self.pets: p.apply_flags()
        elif key == "fps": self.timer.setInterval(int(1000 / max(1, val)))
        elif key == "click_through":
            for p in self.pets: p.mask_dirty = True
        elif key == "perch" and not val: self.tracker.wins = []
        if refresh and hasattr(self, "panel"): self.panel.refresh()

    def apply_preset(self, name):
        if name in PRESETS:
            self.cfg.update(PRESETS[name]); self.cfg["preset"] = name; self.save_timer.start(600); self.panel.refresh()

    def save(self):
        try:
            pos = {p.name: p.feet_pos()[0] for p in self.pets}
            self.cfg["positions"] = pos
            if not self.stashed():
                # only remember a character as hidden when the rest are still up;
                # a fully-hidden screen is 'Hide to tray' and is not a real choice
                self.cfg["hidden"] = [p.name for p in self.pets if not p.isVisible()]
            if self.pets: self.cfg["pos_x"] = self.pets[0].feet_pos()[0]
            SETTINGS_PATH.write_text(json.dumps(self.cfg, indent=2))
        except Exception: pass

    # ---- ui ----
    def summon(self, pet=None):
        g = self.resolve_screen()
        c = QtGui.QCursor.pos()
        if not (g.left() <= c.x() <= g.right() and g.top() <= c.y() <= g.bottom()):
            c = g.center()                                   # cursor is on another monitor
        for p in (self.targets() if pet is None else [pet]):
            p.support = None; p.grounded = False; p.dragging = False
            p.chat_abort(); p.meet_target = None
            fx = self.clamp_x(c.x(), p.W)
            p.set_feet(fx, min(c.y() - 40, g.bottom() - p.H * 0.2))
            p.vx = p.vy = 0; p.air_kind = "thrown"
            p.enter("fall"); p.recall()

    def toggle_pet(self):
        """Hide / show everything. Deliberately NOT persisted - this is a
        temporary 'get them off my screen' action, not a per-character choice.
        """
        vis = not all(p.isVisible() for p in self.pets)
        for p in self.pets: p.show() if vis else p.hide()
        if getattr(self.panel, "sync_char_rows", None): self.panel.sync_char_rows()

    def stashed(self):
        """True while everything is hidden by toggle_pet (so save() can tell them apart)."""
        return bool(self.pets) and not any(p.isVisible() for p in self.pets)

    def show_panel(self):
        self.panel.refresh(); self.panel.sync_char_rows(); self.panel.show(); self.panel.raise_(); self.panel.activateWindow()

    def show_menu(self, pos, p=None):
        p = p or self.active or (self.pets[0] if self.pets else None)
        if p is None: return
        m = QtWidgets.QMenu()
        m.setTitle(p.name)
        for t, fn in (("Poke", p.poke), ("Headpat", p.headpat), ("EX Cut-in", p.do_cutin),
                      ("Tactical Start", p.do_tactical), ("Spin (3D)", p.spin), ("Launch!", p.launch)):
            m.addAction(t, fn)
        am = m.addMenu("Animations")
        for key, (name, _l) in ANIM_KEYS.items(): am.addAction(name, lambda k=key: p.play_anim(k))
        m.addSeparator()
        pm = m.addMenu("Other characters")
        for q in self.pets:
            if q is p: continue
            sm = pm.addMenu(q.name)
            sm.addAction("Make active", lambda pp=q: self.set_active(pp))
            sm.addAction("Summon", lambda pp=q: self.summon(pp))
            sm.addAction("Poke", lambda pp=q: pp.poke())
            sm.addAction("Reset position", lambda pp=q: pp.reset_position())
            a = sm.addAction(f"Talk with {q.name}"); a.setEnabled(self.cfg["chat"])
            a.triggered.connect(lambda _c=False, pp=p, qq=q: self.start_chat(pp, qq))
            a = sm.addAction("Visible"); a.setCheckable(True); a.setChecked(q.isVisible())
            a.toggled.connect(lambda v, pp=q: self.set_pet_visible(pp, v))
        m.addSeparator()
        for label, key in (("Wander", "wander"), ("Look at cursor", "look_at_cursor"), ("Follow cursor", "follow_cursor"),
                           ("Perch on windows", "perch"), ("Speech bubbles", "speech"), ("Particles", "particles"),
                           ("Always on top", "always_on_top"), ("Light follows cursor", "light_follow")):
            a = m.addAction(label); a.setCheckable(True); a.setChecked(bool(self.cfg[key]))
            a.toggled.connect(lambda v, k=key: self.set(k, v, refresh=True))
        sm = m.addMenu("Size")
        for pct in (50, 75, 100, 125, 150, 200): sm.addAction(f"{pct}%", lambda v=pct: self.set("scale", v / 100, refresh=True))
        pm = m.addMenu("Shader preset")
        for name in PRESETS: pm.addAction(name, lambda n=name: self.apply_preset(n))
        m.addSeparator()
        m.addAction("Control Panel...", self.show_panel); m.addAction("Hide to tray", self.toggle_pet); m.addAction("Quit", self.qapp.quit)
        m.exec(pos)

    def setup_tray(self):
        self.tray = None
        if not QtWidgets.QSystemTrayIcon.isSystemTrayAvailable():
            self.show_panel(); return
        self.tray = QtWidgets.QSystemTrayIcon(make_icon(self.active.name if self.active else "A"), self)
        self.tray.setToolTip("Desktop Pets")
        menu = QtWidgets.QMenu()
        menu.addAction("Show / Hide all", self.toggle_pet); menu.addAction("Control Panel...", self.show_panel)
        menu.addSeparator()
        menu.addAction("Summon target to cursor", self.summon)
        menu.addAction("EX Cut-in (target)", lambda: self.each(lambda p: p.do_cutin()))
        menu.addAction("Poke (target)", lambda: self.each(lambda p: p.poke()))
        cm = menu.addMenu("Characters")
        for p in self.pets:
            sub = cm.addMenu(p.name)
            sub.addAction("Make active", lambda pp=p: self.set_active(pp))
            sub.addAction("Summon", lambda pp=p: self.summon(pp))
            sub.addAction("EX Cut-in", lambda pp=p: pp.do_cutin())
            a = sub.addAction("Visible"); a.setCheckable(True); a.setChecked(p.isVisible())
            a.toggled.connect(lambda v, pp=p: self.set_pet_visible(pp, v))
        menu.addSeparator()
        menu.addAction("Quit", self.qapp.quit)
        self.tray_menu = menu; self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda r: self.show_panel() if r in (QtWidgets.QSystemTrayIcon.ActivationReason.DoubleClick,
                                                                          QtWidgets.QSystemTrayIcon.ActivationReason.Trigger) else None)
        self.tray.show()

    # ---- main loop ----
    def tick(self):
        now = time.perf_counter(); dt = min(0.1, now - self.last); self.last = now
        self.fps = 0.9 * self.fps + 0.1 * (1.0 / max(dt, 1e-3))
        try:
            if self.cfg["perch"] and self.tracker.enabled and now - self.trk_t > 0.12:
                self.tracker.refresh(self.active.dpr if self.active else 1.0, self.home.top()); self.trk_t = now
            for p in self.pets:
                if p.isVisible(): p.tick(dt)
            if self.cutin.active: self.cutin.step()
            if self.panel.isVisible() and now - self.status_t > 0.25: self.panel.update_status(); self.status_t = now
            self.errors = 0
        except Exception:
            traceback.print_exc(); self.errors += 1
            if self.errors > 30:
                print("Too many errors, stopping."); self.timer.stop()


def find_models(args):
    """Every .glb next to this script becomes a character (--model limits the list)."""
    if args.model:
        paths = []
        for part in args.model:
            p = Path(part)
            paths.extend(sorted(p.glob("*.glb")) if p.is_dir() else ([p] if p.exists() else []))
        return paths
    return sorted(HERE.glob("*.glb"), key=lambda q: q.name.lower())


def main():
    ap = argparse.ArgumentParser(description="Single-screen desktop pets (one character per .glb)")
    ap.add_argument("--model", nargs="*", help="glb file(s) or folder(s) to use (default: every *.glb next to this script)")
    ap.add_argument("--dump", action="store_true", help="print bones / animations / materials for every model")
    ap.add_argument("--no-mask", action="store_true", help="disable click-through mask (whole window clickable)")
    ap.add_argument("--scale", type=float, default=None); ap.add_argument("--fps", type=int, default=None)
    ap.add_argument("--screen", default=None,
                    help="lock every character to one screen: 0, 1, ... / 'cursor' (default) / 'primary'")
    args = ap.parse_args()
    qapp = QtWidgets.QApplication(sys.argv)
    qapp.setQuitOnLastWindowClosed(False); qapp.setStyleSheet(QSS)
    paths = find_models(args)
    if not paths:
        QtWidgets.QMessageBox.critical(None, "Desktop Pets", f"No .glb models found in:\n{HERE}")
        return 1
    try:
        app = App(qapp, paths, args)
    except Exception as e:
        traceback.print_exc()
        QtWidgets.QMessageBox.critical(None, "Desktop Pets - startup failed",
                                       f"{type(e).__name__}: {e}\n\nSee the console for details.\n"
                                       "Make sure your GPU drivers support OpenGL 3.3.")
        return 1
    return qapp.exec()


if __name__ == "__main__":
    sys.exit(main())
