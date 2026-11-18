"""Regression tests for catalog discovery, model residency, and GLB decoding."""
from __future__ import annotations

import gc
import json
import struct
import tempfile
import threading
import time
import unittest
import weakref
from pathlib import Path

import numpy as np

from bapet.assets import ModelPool, catalog, saved_team
from bapet.gltf import GLTF


class Asset:
    pass


def wait(pool, slot):
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        obj = pool.poll(slot)
        if obj is not None: return obj
        time.sleep(0.002)
    raise AssertionError("model request timed out")


class LoadingTests(unittest.TestCase):
    def setUp(self):
        self.roster = catalog([f"{i:03}.glb" for i in range(200)], lambda p: {})
        self.decodes, self.uploads = [], []
        def decode(slot):
            self.decodes.append((slot, threading.get_ident()))
            return Asset()
        self.pool = ModelPool(decode, lambda obj: self.uploads.append(threading.get_ident()))
        self.addCleanup(self.pool.close)

    def test_200_catalog_entries_without_model_io(self):
        # These files do not exist. Any attempt to open one would fail.
        self.assertEqual(len(self.roster), 200)
        self.assertEqual([s.index for s in self.roster], list(range(200)))
        self.assertFalse(self.decodes or self.uploads)
        self.assertFalse(any(x.isVisible() for x in self.roster))

    def test_deduplication_parts_and_display_name_collisions(self):
        slots = catalog(["a/A_B.glb", "a/A_B.glb", "b/A-B.glb", "part.glb"],
                        lambda p: {"pet": p.stem != "part"})
        self.assertEqual([s.name for s in slots], ["A B", "A B (2)"])

    def test_saved_team_does_not_expand_with_library(self):
        self.assertEqual(saved_team(self.roster, {}), self.roster[:1])
        self.assertEqual(saved_team(self.roster, {"deployed": []}), [])
        self.assertEqual(saved_team(self.roster, {"deployed": ["017", "deleted"]}), [self.roster[17]])
        old = {"positions": {"001": 250, "002": 500}, "hidden": ["001"]}
        self.assertEqual(saved_team(self.roster, old), [self.roster[2]])

    def test_single_flight_thread_ownership_and_eviction(self):
        s = self.roster[0]
        pinned = wait(self.pool, s)
        self.assertIs(wait(self.pool, s), pinned)
        for other in self.roster[1:8]: wait(self.pool, other)
        gc.collect()
        self.assertEqual(len(self.pool.warm), 2)
        self.assertEqual(len(self.pool.live), 3)
        self.assertIs(self.pool.peek(s), pinned)
        self.assertEqual(len(self.decodes), 8)
        self.assertTrue(all(t != threading.get_ident() for _, t in self.decodes))
        self.assertTrue(all(t == threading.get_ident() for t in self.uploads))
        r = weakref.ref(pinned); del pinned; gc.collect()
        self.assertIsNone(r())

    def test_scrolled_away_preview_not_uploaded(self):
        a, b = self.roster[:2]
        self.pool.poll(a)
        self.pool.pending[1].result(timeout=4)
        wait(self.pool, b)
        self.assertEqual(len(self.uploads), 1)
        self.assertIsNone(self.pool.peek(a))

    def test_bad_model_isolated_and_retriable(self):
        old = self.pool.decode
        def fail(slot): raise ValueError("bad GLB")
        self.pool.decode = fail
        with self.assertRaisesRegex(RuntimeError, "bad GLB"):
            wait(self.pool, self.roster[0])
        self.pool.decode = old
        self.assertIsNotNone(wait(self.pool, self.roster[1]))
        self.pool.errors.pop(self.roster[0])
        self.assertIsNotNone(wait(self.pool, self.roster[0]))

    def test_upload_failure_does_not_publish_partial_model(self):
        def fail(obj): raise ValueError("upload failed")
        self.pool.upload = fail
        with self.assertRaisesRegex(RuntimeError, "upload failed"):
            wait(self.pool, self.roster[0])
        self.assertFalse(self.pool.warm or self.pool.live)

    def test_close_is_idempotent(self):
        self.pool.close(); self.pool.close()
        with self.assertRaisesRegex(RuntimeError, "closed"):
            self.pool.poll(self.roster[0])

    def test_does_not_accept_gpu_calls_from_worker(self):
        future = self.pool.worker.submit(self.pool.poll, self.roster[0])
        with self.assertRaisesRegex(RuntimeError, "graphics thread"):
            future.result(timeout=4)

    def test_interleaved_normalized_accessor_and_truncated_glb(self):
        doc = {"asset": {"version": "2.0"}, "buffers": [{"byteLength": 8}],
               "bufferViews": [{"buffer": 0, "byteLength": 8, "byteStride": 4}],
               "accessors": [{"bufferView": 0, "componentType": 5120, "type": "VEC2",
                              "count": 2, "normalized": True}]}
        j = json.dumps(doc).encode(); j += b" " * (-len(j) % 4)
        data = struct.pack("<II", len(j), 0x4e4f534a) + j
        data += struct.pack("<II", 8, 0x004e4942) + bytes([128, 127, 0, 0, 0, 64, 0, 0])
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "test.glb"
            p.write_bytes(struct.pack("<4sII", b"glTF", 2, 12 + len(data)) + data)
            np.testing.assert_allclose(GLTF(p).accessor(0), [[-1, 1], [0, 64 / 127]])
            p.write_bytes(p.read_bytes()[:-1])
            with self.assertRaises(ValueError): GLTF(p)


if __name__ == "__main__":
    unittest.main()