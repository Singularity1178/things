"""Character: one .glb plus its own renderer, clips and fit."""
from __future__ import annotations

from pathlib import Path

from .combat_config import MAG_SIZE
from .combat_data import FIRING_RANGE_DEFAULT, FIRING_RANGE_SCALE, ammo_for, firing_range_for
from .config import ANIM_KEYS
from .mathutil import clamp
from .model import Model
from .profiles import PROFILE_DEFAULTS, RIDE_FADE


class Character:
    def __init__(self, path, index=0, dump=False, profile=None):
        self.path = Path(path)
        self.index = index
        self.name = self.path.stem.replace("_", " ").replace("-", " ").strip() or f"Pet {index + 1}"
        if len(self.name) > 28: self.name = self.name[:28]
        print(f"Loading {self.path.name} as '{self.name}' ...")
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

        fits = {}
        def fit_for(clip):
            if clip not in fits:
                fits[clip] = self.model.compute_fit(self.model.posed0(clip))
            return fits[clip]

        def window_for(wscale, waspect, fit_name, idle_clip):
            fit, fc = fit_for(idle_clip), idle_clip
            if fit_name:
                c = self.model.find_clip(fit_name)
                if c is not None:
                    fit, fc = fit_for(c), c
                else:
                    print(f"[warn] {self.path.name}: fit clip '{fit_name}' not found - "
                          f"the window is sized for '{idle_clip.name}' instead.")
            frac = clamp(fit_for(stock)["H"] / max(fit["H"], 1e-6), 0.05, 1.0)
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
        self.renderer = None            # created lazily (one GL context is enough)
        # combat stats from combat_index.json (matched by .glb file name), else the defaults
        self.firing_range, found = firing_range_for(self.path)
        self.range_scale = clamp(self.firing_range / FIRING_RANGE_DEFAULT, *FIRING_RANGE_SCALE)
        print(f"  combat: firing range {self.firing_range:.0f} cm "
              f"({'combat_index.json' if found else 'default, not in index'}), spacing x{self.range_scale:.2f}")
        self.ammo_per_attack, self.ammo_to_reload, ammo_found = ammo_for(self.path)
        mag_txt = f"{self.ammo_to_reload}-round magazine" if self.ammo_to_reload else f"random {MAG_SIZE[0]}-{MAG_SIZE[1]}-round magazine"
        print(f"  ammo: {self.ammo_per_attack} bullet(s) per attack, {mag_txt} "
              f"({'combat_index.json' if ammo_found else 'default, no usable ammo stats'})")
