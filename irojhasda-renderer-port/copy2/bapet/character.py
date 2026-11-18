"""Character: one .glb plus its own renderer, clips and fit.

Loading on demand
-----------------
A Character is cheap to create: its name, its profile and its combat stats all come from the
file NAME (and combat_index.json), so a roster of hundreds costs nothing at start-up. The .glb
itself is only read the first time something needs it - `ch.model`, `ch.clips`, `ch.fit`,
`ch.renderer` ... all load it transparently on first touch, so no caller has to know:

    * Desktop Pets   the students you deploy (Pet.ensure_live)
    * Battle Arena / Campaign   the students actually fighting (Fighter reads ch.model)
    * Student Select            the students standing on the stage; cards use a cached portrait

    ch.loaded        is the file in memory right now?
    ch.load()        read it now (no-op when loaded; raises if the file is unusable)
    ch.unload()      drop the model and free its GPU objects; the next touch loads it again
    ch.pin()/unpin() "in use, do not unload" (a battle pins its fighters)
    ch.touch()       mark it as just used (App.trim_models unloads the least recently used)

`Character(path)` on its own still loads straight away, as it always did; the App passes
lazy=True.
"""
from __future__ import annotations

import time
import traceback
from pathlib import Path

from .combat_config import MAG_SIZE
from .combat_data import FIRING_RANGE_DEFAULT, FIRING_RANGE_SCALE, ammo_for, firing_range_for
from .config import ANIM_KEYS
from .mathutil import clamp
from .model import Model
from .profiles import PROFILE_DEFAULTS, RIDE_FADE


class Character:
    ctx = None            # the shared GL context; App sets it once, renderers are built on it

    # What only exists while the .glb is loaded. Reading one of these loads the file.
    HEAVY = frozenset(("model", "clips", "win", "fit", "fit_clip", "body_frac",
                       "ride_clips", "ride_win", "ride_fade"))

    def __init__(self, path, index=0, dump=False, profile=None, lazy=False):
        self.path = Path(path)
        self.index = index
        self.name = self.path.stem.replace("_", " ").replace("-", " ").strip() or f"Pet {index + 1}"
        if len(self.name) > 28: self.name = self.name[:28]
        p = dict(PROFILE_DEFAULTS, **(profile or {}))
        self.profile = p
        # A window of her own on top of the shared --scale one, for a model that
        # does not fit the stock 380x520 (see load_profile / iroha.py).
        self.window_scale, self.window_aspect = p["window_scale"], p["window_aspect"]
        # How shooting treats HER (Iroha's tank shrugs bullets off): see PROFILE_DEFAULTS.
        self.shot_ragdoll_chance = p.get("shot_ragdoll_chance")
        self.shot_give_up = p.get("shot_give_up")
        self.hit_lines = list(p.get("hit_lines") or [])
        self.give_up_lines = list(p.get("give_up_lines") or [])
        self.bullet = p.get("bullet")
        # combat stats from combat_index.json (matched by .glb file name), else the defaults
        self.firing_range, self._range_found = firing_range_for(self.path)
        self.range_scale = clamp(self.firing_range / FIRING_RANGE_DEFAULT, *FIRING_RANGE_SCALE)
        self.ammo_per_attack, self.ammo_to_reload, self._ammo_found = ammo_for(self.path)
        # ---- load state ----
        self.lazy = bool(lazy)
        self.loaded = False
        self.failed = None            # the error that stopped her loading, so it is not retried every frame
        self.pins = 0                 # > 0: in use by something that is not a visible pet (a battle)
        self.used_at = 0.0            # perf_counter of the last use, for App.trim_models
        self.generation = 0           # bumped on every unload: anything caching her clips re-reads them
        self.load_ms = 0.0
        self._dump = bool(dump)
        self._light = frozenset(self.__dict__) | {"_light"}      # everything above survives unload()
        if not self.lazy:
            self.load()

    # ------------------------------------------------------------------ on demand
    def __getattr__(self, name):
        # Only reached when the attribute does not exist yet.
        if name in Character.HEAVY:
            d = self.__dict__
            if "_light" in d and not d.get("loaded") and not d.get("_loading"):
                self.load()
                if name in d: return d[name]
        elif name == "renderer":
            # Built on first use, on the shared context. With no context (tests, tools that
            # only want the data) it stays None, as it was before App handed one over.
            d = self.__dict__
            if "_light" not in d or d.get("_loading"): return None
            if Character.ctx is None: return None
            from .renderer import Renderer
            r = d["renderer"] = Renderer(self.model, self.fit, Character.ctx)
            return r
        raise AttributeError(f"'Character' object has no attribute '{name}'")

    @property
    def has_ride(self):
        """Does she bring a vehicle (Iroha's tank)? Answered from the profile while the file is
        not loaded, so listing a roster does not read every .glb just to find out."""
        if self.loaded: return self.__dict__.get("ride_clips") is not None
        return bool((self.profile.get("ride") or {}).get("clips"))

    def touch(self):
        self.used_at = time.perf_counter()

    def pin(self):
        self.pins += 1; self.used_at = time.perf_counter()

    def unpin(self):
        self.pins = max(0, self.pins - 1); self.used_at = time.perf_counter()

    def unload(self):
        """Forget the model: numpy data goes with the last reference, GPU objects are released
        here (moderngl does not free them by itself). Cheap metadata stays, so she is still in
        every roster and loads again the next time she is needed."""
        if not self.loaded: return
        d = self.__dict__
        r = d.get("renderer")
        if r is not None:
            try: r.release()
            except Exception: traceback.print_exc()
        for k in [k for k in d if k not in self._light]:
            del d[k]
        self.window_scale, self.window_aspect = self.profile["window_scale"], self.profile["window_aspect"]
        self.loaded = False
        self.generation += 1

    def load(self):
        """Read the .glb and work out her clips and her window. No-op once loaded."""
        if self.loaded: return self
        if self.failed is not None:
            raise RuntimeError(f"{self.path.name} could not be loaded earlier: {self.failed}")
        self._loading = True
        t0 = time.perf_counter()
        try:
            self._load()
        except Exception as e:
            self.failed = f"{type(e).__name__}: {e}"
            d = self.__dict__
            for k in [k for k in d if k not in self._light]:
                del d[k]
            raise
        finally:
            self.__dict__.pop("_loading", None)
        self.loaded = True
        self.used_at = time.perf_counter()
        self.load_ms = (self.used_at - t0) * 1000.0
        print(f"  loaded {self.path.name} in {self.load_ms:.0f} ms")
        return self

    def _load(self):
        p = self.profile
        dump = self._dump
        print(f"Loading {self.path.name} as '{self.name}' ...")
        self.model = Model(self.path)
        if dump: self.model.dump()
        # Textures her own file does not ship, borrowed from another .glb:
        # prim-name fragment -> {"glb": path, "image": index, "flip_v": bool}.
        # Done before anything asks for an image, and the src becomes a private
        # string key rather than an index into this file (see foreign_image).
        for frag, spec in (p.get("textures") or {}).items():
            key = f"profile:{frag}"
            hit = [q for q in self.model.prims if frag in q.name]
            if not hit:
                print(f"[warn] {self.path.name}: texture rule '{frag}' matches no primitive")
                continue
            if not isinstance(spec, dict) or "glb" not in spec or "image" not in spec:
                print(f"[warn] {self.path.name}: texture rule '{frag}' needs "
                      f"{{'glb': path, 'image': index}} - skipped")
                continue
            for q in hit: q.src = key
            self.model.tex_from[key] = spec
            print(f"  texture: {len(hit)} primitive(s) matching '{frag}' <- "
                  f"{Path(str(spec['glb'])).name} image {spec['image']}"
                  + (", v flipped" if spec.get("flip_v") else ""))
        self.clips = {}
        fallback = next(iter(self.model.clips.values()), self.model.rest_clip)
        for key, (name, _loop) in ANIM_KEYS.items():
            if key in p["anim"]: name = p["anim"][key]       # her own clip for this beat
            c = self.model.find_clip(name)
            if c is None and key == "reload":                  # optional: no stand-in clip, Pet refills without one
                print(f"[warn] {self.path.name}: animation '{name}' not found - reloads will use Formation_Idle.")
                continue
            if c is None:
                print(f"[warn] {self.path.name}: animation '{name}' not found - using '{fallback.name}'.")
                c = fallback
            self.clips[key] = c
        # Her own attack clips (profile `attack_clips`, e.g. Tank_Normal_Attack_*): they replace what attack_sets
        # would find by name, which skips anything with a Tank_ prefix. attack_sets caches on the model, so
        # seeding that cache is all it takes. A stance needs an 'ing' clip to be able to fire, like any other.
        if p.get("attack_clips"):
            sets = {}
            for stance, phases in p["attack_clips"].items():
                got = {}
                for ph, cname in phases.items():
                    c = self.model.find_clip(cname)
                    if c is None: print(f"[warn] {self.path.name}: attack clip '{cname}' ({stance}/{ph}) not found")
                    else: got[ph] = c
                if "ing" in got: sets[stance] = got
                else: print(f"[warn] {self.path.name}: attack stance '{stance}' has no usable 'ing' clip - dropped")
            if sets:
                self.model._atk_sets = sets
                print("  attacks: " + ", ".join(k + "[" + "/".join(v) + "]" for k, v in sets.items()) + " (from profile)")
        idle = self.clips["idle"]
        # --- her window: a camera, plus how much of it is her own body ----------
        # A function of the preset rather than a block that runs once, because a
        # profile with `ride` clips needs a second, different window (the one her
        # vehicle needs). Measured on the STOCK idle clip either way: a window
        # sized for a pose with a tank in it must not then measure the tank
        # against itself. Only the ragdoll reads the share - it sizes capsule
        # radii off her height, and a window full of tank would give her
        # limb-thick bones.
        stock = self.model.find_clip(ANIM_KEYS["idle"][0]) or idle

        def window_for(wscale, waspect, fit_name, idle_clip):
            fit, fc = self.model.compute_fit(self.model.posed0(idle_clip)), idle_clip
            if fit_name:
                c = self.model.find_clip(fit_name)
                if c is not None:
                    fit, fc = self.model.compute_fit(self.model.posed0(c)), c
                else:
                    print(f"[warn] {self.path.name}: fit clip '{fit_name}' not found - "
                          f"the window is sized for '{idle_clip.name}' instead.")
            # (an ordinary model's window IS its stock idle pose: do not measure the same pose twice)
            stock_h = fit["H"] if fc is stock else self.model.compute_fit(self.model.posed0(stock))["H"]
            frac = clamp(stock_h / max(fit["H"], 1e-6), 0.05, 1.0)
            return dict(scale=wscale, aspect=waspect, fit=fit, clip=fc, body_frac=frac)

        self.win = window_for(p["window_scale"], p["window_aspect"], p["fit_clip"], idle)
        self.fit, self.fit_clip, self.body_frac = self.win["fit"], self.win["clip"], self.win["body_frac"]
        if self.body_frac < 0.999:
            print(f"  window: sized for '{self.fit_clip.name}' ({self.fit['H']:.2f} tall), "
                  f"her own body is {self.body_frac * 100:.0f}% of it")
        # --- riding: a second set of clips + window, only while she is fighting ---
        # Anything `ride` does not name keeps the clip above. The vehicle geometry
        # is found here too and flagged on the primitives, so the ride transition
        # knows what to fade (Model.ride_prims, Pet.set_ride).
        rd, self.ride_clips, self.ride_win, self.ride_fade = p.get("ride") or {}, None, None, 0.0
        if rd.get("clips"):
            rc = dict(self.clips)
            for key, name in rd["clips"].items():
                c = self.model.find_clip(name) if key in rc else None
                if c is None: print(f"[warn] {self.path.name}: ride animation '{name}' not found - "
                                    f"she keeps '{rc[key].name if key in rc else 'nothing'}' while riding.")
                else: rc[key] = c
            if all(rc[k] is v for k, v in self.clips.items()):
                # Nothing to switch to. Taking the ride window anyway would frame
                # her at the vehicle's size with no vehicle in it, so drop it all.
                print(f"[warn] {self.path.name}: no ride clip is different from the one she already "
                      f"plays - she never mounts anything.")
            else:
                self.ride_win = window_for(rd["window_scale"], rd["window_aspect"], rd["fit_clip"],
                                           rc.get("idle", idle))
                veh = {}
                for c in set(rc.values()):                   # any riding clip may be the one with the vehicle
                    for q in self.model.ride_prims(self.clips["idle"], c): veh[id(q)] = q
                if len(veh) >= len(self.model.prims):
                    print(f"[warn] {self.path.name}: every primitive reads as part of the vehicle - "
                          f"the ride transition will hard-cut it instead of fading it.")
                    veh = {}
                for q in veh.values(): q.vehicle = True
                self.ride_clips, self.ride_fade = rc, float(rd.get("fade", RIDE_FADE))
                print(f"  ride: {sum(1 for k, v in rc.items() if self.clips.get(k) is not v)} clip(s) swapped, "
                      f"window x{self.ride_win['scale']:.2f} (aspect x{self.ride_win['aspect']:.2f}) sized for "
                      f"'{self.ride_win['clip'].name}', {len(veh)} vehicle primitive(s) fading over {self.ride_fade:.2f}s"
                      + (f", her body is {self.ride_win['body_frac'] * 100:.0f}% of it"
                         if self.ride_win["body_frac"] < 0.999 else ""))
        # combat stats were read in __init__ (they only need the file name)
        found, ammo_found = self._range_found, self._ammo_found
        print(f"  combat: firing range {self.firing_range:.0f} cm "
              f"({'combat_index.json' if found else 'default, not in index'}), spacing x{self.range_scale:.2f}")
        mag_txt = f"{self.ammo_to_reload}-round magazine" if self.ammo_to_reload else f"random {MAG_SIZE[0]}-{MAG_SIZE[1]}-round magazine"
        print(f"  ammo: {self.ammo_per_attack} bullet(s) per attack, {mag_txt} "
              f"({'combat_index.json' if ammo_found else 'default, no usable ammo stats'})")
