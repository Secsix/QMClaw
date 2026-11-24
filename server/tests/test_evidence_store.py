"""演练证据通道（BACKLOG O8）的单测：落盘 / 过期 / 合并 / 冲突 / 清空。

只测 `services/common/evidence_store.py` 与
`services/common/state_evidence.merge_drill_evidence` 这两个纯模块，
端点层在 test_quantum_evidence_endpoints.py 里测。
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest

from services.common.evidence_store import (
    DEFAULT_SOURCE,
    DEFAULT_TTL_S,
    STORE_FILENAME,
    EvidenceStore,
    classify_value,
    is_active,
    normalize_entry,
)
from services.common.state_evidence import merge_drill_evidence

NOW = 1_800_000_000.0


class TempStoreCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="qmclaw-evidence-test-")
        self.addCleanup(self._tmp.cleanup)
        self.dir = self._tmp.name
        self.path = os.path.join(self.dir, STORE_FILENAME)

    def store(self) -> EvidenceStore:
        return EvidenceStore(path=self.path)


class TestNormalizeEntry(TempStoreCase):
    def test_defaults(self) -> None:
        e = normalize_entry("contrast", 0.85, now=NOW)
        self.assertEqual(e["key"], "contrast")
        self.assertEqual(e["value"], 0.85)
        self.assertEqual(e["source"], DEFAULT_SOURCE)
        self.assertAlmostEqual(e["created_at"], NOW)
        self.assertAlmostEqual(e["expires_at"], NOW + DEFAULT_TTL_S)

    def test_ttl_zero_means_never_expire(self) -> None:
        e = normalize_entry("freq_locked", True, ttl_s=0, now=NOW)
        self.assertIsNone(e["expires_at"])
        self.assertTrue(is_active(e, NOW + 10 * 365 * 86400))

    def test_ttl_negative_also_never_expire(self) -> None:
        e = normalize_entry("freq_locked", True, ttl_s=-5, now=NOW)
        self.assertIsNone(e["expires_at"])

    def test_key_normalized_and_value_kept(self) -> None:
        e = normalize_entry("  Contrast ", "0.5", now=NOW)
        self.assertEqual(e["key"], "contrast")
        # 值不在这里做类型转换，交给 state_evidence 的数值解析
        self.assertEqual(e["value"], "0.5")

    def test_source_and_meta_preserved(self) -> None:
        e = normalize_entry(
            "t1_us", 45.0, source="demo", run_id="demo-1", note="剧本写入", now=NOW
        )
        self.assertEqual(e["source"], "demo")
        self.assertEqual(e["run_id"], "demo-1")
        self.assertEqual(e["note"], "剧本写入")

    def test_created_at_override(self) -> None:
        e = normalize_entry("contrast", 0.8, now=NOW, created_at=NOW - 60)
        self.assertAlmostEqual(e["created_at"], NOW - 60)

    def test_expired_entry_not_active(self) -> None:
        e = normalize_entry("contrast", 0.8, ttl_s=10, now=NOW)
        self.assertTrue(is_active(e, NOW + 5))
        self.assertFalse(is_active(e, NOW + 11))


class TestClassifyValue(unittest.TestCase):
    def test_bool_first(self) -> None:
        # bool 必须先于 int 判断（Python 里 bool 是 int 的子类）
        self.assertEqual(classify_value(True), "bool")
        self.assertEqual(classify_value(False), "bool")

    def test_numbers(self) -> None:
        self.assertEqual(classify_value(0.85), "number")
        self.assertEqual(classify_value(45), "number")

    def test_text_and_other(self) -> None:
        self.assertEqual(classify_value("abc"), "string")
        self.assertEqual(classify_value(None), "other")
        self.assertEqual(classify_value([1]), "other")


class TestStoreBasics(TempStoreCase):
    def test_missing_file_is_empty(self) -> None:
        doc = self.store().load()
        self.assertEqual(doc["qubits"], {})
        self.assertEqual(doc["version"], 1)

    def test_broken_json_is_empty(self) -> None:
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write("{not json")
        self.assertEqual(self.store().load()["qubits"], {})

    def test_merge_and_read(self) -> None:
        s = self.store()
        written = s.merge("q0", {"freq_locked": True, "contrast": 0.85}, now=NOW)
        self.assertTrue(written["ok"])
        self.assertEqual(written["count"], 2)
        self.assertEqual(len(written["entries"]), 2)

        values = s.values("q0", now=NOW)
        self.assertEqual(values["contrast"], 0.85)
        self.assertTrue(values["freq_locked"])

        sources = s.sources("q0", now=NOW)
        self.assertEqual(sources["contrast"]["source"], DEFAULT_SOURCE)

        entries = s.entries("q0", now=NOW)
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]["key"], "contrast")

    def test_save_is_atomic_and_persists(self) -> None:
        s = self.store()
        s.merge("q0", {"contrast": 0.5}, now=NOW)
        with open(self.path, encoding="utf-8") as fh:
            doc = json.load(fh)
        self.assertEqual(doc["version"], 1)
        self.assertIn("q0", doc["qubits"])
        self.assertEqual(doc["qubits"]["q0"]["contrast"]["value"], 0.5)
        # 无残留临时文件
        leftovers = [n for n in os.listdir(self.dir) if n not in (STORE_FILENAME,)]
        self.assertEqual(leftovers, [])

    def test_merge_overwrites_same_key(self) -> None:
        s = self.store()
        s.merge("q0", {"contrast": 0.2}, now=NOW)
        s.merge("q0", {"contrast": 0.9}, now=NOW + 10)
        self.assertEqual(s.values("q0", now=NOW + 20)["contrast"], 0.9)

    def test_reload_from_disk(self) -> None:
        self.store().merge("q0", {"contrast": 0.7}, now=NOW)
        fresh = self.store()
        self.assertEqual(fresh.values("q0", now=NOW)["contrast"], 0.7)

    def test_qubits_are_isolated(self) -> None:
        s = self.store()
        s.merge("q0", {"contrast": 0.9}, now=NOW)
        s.merge("q1", {"contrast": 0.2}, now=NOW)
        self.assertEqual(s.values("q0", now=NOW)["contrast"], 0.9)
        self.assertEqual(s.values("q1", now=NOW)["contrast"], 0.2)
        self.assertEqual(len(s.summary()["qubits"]), 2)
        self.assertEqual(s.summary()["total"], 2)


class TestStoreExpiryAndRemoval(TempStoreCase):
    def test_expired_entries_filtered(self) -> None:
        s = self.store()
        s.merge("q0", {"contrast": 0.9}, ttl_s=10, now=NOW)
        self.assertEqual(s.values("q0", now=NOW + 5)["contrast"], 0.9)
        self.assertEqual(s.values("q0", now=NOW + 20), {})
        # include_expired 仍能看到，用于排障
        all_entries = s.entries("q0", now=NOW + 20, include_expired=True)
        self.assertEqual(len(all_entries), 1)
        self.assertFalse(all_entries[0]["active"])

    def test_remove_keys(self) -> None:
        s = self.store()
        s.merge("q0", {"contrast": 0.9, "t1_us": 45.0}, now=NOW)
        removed = s.remove("q0", ["contrast"])
        self.assertTrue(removed["ok"])
        self.assertEqual(removed["removed"], ["contrast"])
        values = s.values("q0", now=NOW)
        self.assertNotIn("contrast", values)
        self.assertEqual(values["t1_us"], 45.0)

    def test_reset_one_qubit(self) -> None:
        s = self.store()
        s.merge("q0", {"contrast": 0.9}, now=NOW)
        s.merge("q1", {"contrast": 0.2}, now=NOW)
        cleared = s.reset("q0")
        self.assertEqual(cleared["cleared"], ["q0"])
        self.assertEqual(cleared["removed"], ["contrast"])
        self.assertEqual(s.values("q0", now=NOW), {})
        self.assertEqual(s.values("q1", now=NOW)["contrast"], 0.2)

    def test_reset_all(self) -> None:
        s = self.store()
        s.merge("q0", {"contrast": 0.9}, now=NOW)
        s.merge("q1", {"contrast": 0.2}, now=NOW)
        self.assertEqual(sorted(s.reset()["cleared"]), ["q0", "q1"])
        self.assertEqual(s.load()["qubits"], {})

    def test_set_entry_direct(self) -> None:
        s = self.store()
        entry = s.set_entry("q0", "contrast", 0.42, source="demo", now=NOW)
        self.assertEqual(entry["source"], "demo")
        self.assertEqual(s.values("q0", now=NOW)["contrast"], 0.42)
        self.assertEqual(s.sources("q0", now=NOW)["contrast"]["source"], "demo")


class TestMergeDrillEvidence(unittest.TestCase):
    def test_drill_adds_missing_keys(self) -> None:
        evidence, sources, conflicts = merge_drill_evidence(
            {"contrast": 0.9},
            {"contrast": {"source": "ledger"}},
            {"t1_us": 45.0},
            {"t1_us": {"source": "manual"}},
        )
        self.assertEqual(evidence["t1_us"], 45.0)
        self.assertEqual(sources["t1_us"]["source"], "manual")
        self.assertEqual(conflicts, [])

    def test_real_evidence_wins(self) -> None:
        evidence, sources, conflicts = merge_drill_evidence(
            {"contrast": 0.9},
            {"contrast": {"source": "ledger"}},
            {"contrast": 0.2},
            {"contrast": {"source": "manual"}},
        )
        self.assertEqual(evidence["contrast"], 0.9)
        self.assertEqual(sources["contrast"]["source"], "ledger")
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0]["key"], "contrast")

    def test_same_value_no_conflict(self) -> None:
        _, _, conflicts = merge_drill_evidence(
            {"contrast": 0.9},
            None,
            {"contrast": 0.9},
            None,
        )
        self.assertEqual(conflicts, [])

    def test_does_not_mutate_inputs(self) -> None:
        base = {"contrast": 0.9}
        sources = {"contrast": {"source": "ledger"}}
        merge_drill_evidence(base, sources, {"t1_us": 1.0}, None)
        self.assertEqual(base, {"contrast": 0.9})
        self.assertEqual(sources, {"contrast": {"source": "ledger"}})

    def test_empty_drill_is_noop(self) -> None:
        evidence, sources, conflicts = merge_drill_evidence(
            {"contrast": 0.9}, {"contrast": {"source": "ledger"}}, {}, {}
        )
        self.assertEqual(evidence, {"contrast": 0.9})
        self.assertEqual(conflicts, [])


if __name__ == "__main__":
    unittest.main()
