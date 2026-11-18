"""Cheap roster records and a single-flight, owner-thread model working set."""
from __future__ import annotations

from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from threading import get_ident
from weakref import WeakValueDictionary


@dataclass(eq=False)
class Student:
    path: Path
    index: int
    name: str
    profile: dict = field(repr=False)
    pet: object = field(default=None, repr=False)

    @property
    def ride_clips(self):
        return (self.profile.get("ride") or {}).get("clips") or None

    def isVisible(self):
        return self.pet is not None and self.pet.isVisible()


def catalog(paths, read_profile):
    records, seen, names = [], set(), set()
    for path in map(Path, paths):
        path = path.resolve()
        if path in seen:
            continue
        seen.add(path)
        profile = read_profile(path)
        if not profile.get("pet", True):
            continue
        base = path.stem.replace("_", " ").replace("-", " ").strip()[:28] or "Student"
        name, suffix = base, 1
        while name.casefold() in names:
            suffix += 1
            name = f"{base} ({suffix})"
        names.add(name.casefold())
        records.append(Student(path, len(records), name, profile))
    return records


def saved_team(roster, cfg):
    saved = cfg.get("deployed")
    if isinstance(saved, list):
        names = set(saved)
        return [s for s in roster if s.name in names]
    # Old versions saved positions for every known student. Newly added GLBs
    # must not silently join a previously saved desktop.
    hidden = set(cfg.get("hidden") or ())
    previous = set(cfg.get("positions") or ())
    visible = [s for s in roster if s.name in previous and s.name not in hidden]
    if previous and visible:
        return visible
    return next(([s] for s in roster if s.name not in hidden), list(roster[:1]))


class ModelPool:
    """One CPU decode in flight; upload only on the graphics-context thread.

    A pet, fighter, or visible stage slot owns its character normally. Weak
    lookup shares those live objects; only two additional warm objects are
    retained. There is no background queue walking through the catalog.
    """

    def __init__(self, decode, upload, capacity=2):
        self.decode, self.upload = decode, upload
        self.capacity = max(0, capacity)
        self.live = WeakValueDictionary()
        self.warm = OrderedDict()
        self.errors = {}
        self.pending = None
        self.worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="glb-decode")
        self.owner = get_ident()
        self.closed = False

    def _check(self):
        if get_ident() != self.owner:
            raise RuntimeError("ModelPool must be used on the graphics thread")
        if self.closed:
            raise RuntimeError("ModelPool is closed")

    def peek(self, student):
        self._check()
        obj = self.live.get(student)
        if student in self.warm:
            self.warm.move_to_end(student)
        return obj

    def poll(self, student):
        """Return a character, or None while decoding. Never wait for a worker."""
        self._check()
        if student in self.errors:
            raise RuntimeError(self.errors[student])
        obj = self.peek(student)
        if obj is not None:
            return obj
        if self.pending is not None:
            key, future = self.pending
            if not future.done():
                return None
            self.pending = None
            try:
                obj = future.result()
                # A scrolled-away preview never gets an unnecessary GPU upload.
                if key is student:
                    self.upload(obj)
                    self.live[key] = obj
                    self.warm[key] = obj
                    while len(self.warm) > self.capacity:
                        self.warm.popitem(last=False)
                    return obj
            except Exception as exc:
                message = f"{key.path.name}: {type(exc).__name__}: {exc}"
                self.errors[key] = message
                if key is student:
                    raise RuntimeError(message) from exc
        self.pending = student, self.worker.submit(self.decode, student)
        return None

    def abandon(self):
        # A running CPU decode may finish, but nobody uploads or retains it.
        if self.pending is not None:
            self.pending[1].cancel()
            self.pending = None

    def close(self):
        if self.closed:
            return
        self._check()
        self.closed = True
        self.worker.shutdown(wait=True, cancel_futures=True)
        self.pending = None
        self.warm.clear()
        self.live.clear()