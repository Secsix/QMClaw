"""演练证据通道的端点级测试（`/qubit/evidence`、`/qubit/evidence/reset`）。

不打网络：直接 `object.__new__(QuantumService)` 调 handler，并把
`_evidence_store()` 换成指向临时目录的库（不碰真实 `data/state_ledger`）。

守住四件事：
1. 写入 → 读到；写入即持久化（换一个 store 实例仍能读到）；
2. 证据进 `/qubit/state` 与 `/qubit/route` 的判定（真实台账为空时，能把状态推起来）；
3. 真实台账证据优先（同一 key 有真实值时演练值被丢弃并记 conflict）；
4. reset 清得干净，缺 name 报 validation_error。
"""
from __future__ import annotations

import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

SERVER_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from services.common import runrecord  # noqa: E402
from services.common.evidence_store import EvidenceStore  # noqa: E402
from services.quantum_service.server import QuantumService  # noqa: E402


class EvidenceEndpointCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="qmclaw-evidence-ep-")
        self.addCleanup(self._tmp.cleanup)
        self.path = os.path.join(self._tmp.name, "evidence.json")
        self.service = object.__new__(QuantumService)
        self.service._evidence_store = lambda: EvidenceStore(path=self.path)  # type: ignore[assignment]
        # 台账默认是空的：真实证据不参与，确保测试只看演练通道
        patcher = mock.patch.object(
            runrecord.RunRecordWriter, "list_records", return_value=[]
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    # ── 读/写 ────────────────────────────────────────────────────────────

    def test_write_then_read(self) -> None:
        written = self.service._handle_qubit_evidence(
            {"name": "q0", "entries": {"freq_locked": True, "contrast": 0.85}}
        )
        self.assertTrue(written["success"])
        self.assertEqual(written["count"], 2)
        self.assertEqual(os.path.dirname(written["path"]), self._tmp.name)

        read = self.service._handle_qubit_evidence({"name": "q0"})
        self.assertTrue(read["success"])
        self.assertEqual(read["count"], 2)
        keys = sorted(e["key"] for e in read["entries"])
        self.assertEqual(keys, ["contrast", "freq_locked"])

    def test_persisted_across_store_instances(self) -> None:
        self.service._handle_qubit_evidence({"name": "q0", "entries": {"contrast": 0.9}})
        fresh = EvidenceStore(path=self.path)
        self.assertEqual(fresh.values("q0")["contrast"], 0.9)

    def test_top_level_scalars_accepted(self) -> None:
        # 也允许把证据键直接写在顶层（演示时手敲 curl 更省事）
        res = self.service._handle_qubit_evidence(
            {"name": "q0", "contrast": 0.7, "t1_us": 40.0, "note": "手敲"}
        )
        self.assertTrue(res["success"])
        self.assertEqual(res["count"], 2)
        self.assertEqual(res["entries"][0]["note"] if res["entries"] else "手敲", res["entries"][0]["note"])

    def test_source_and_ttl_recorded(self) -> None:
        res = self.service._handle_qubit_evidence(
            {
                "name": "q0",
                "entries": {"contrast": 0.6},
                "source": "demo",
                "ttl_s": 60,
                "note": "剧本阶段 3",
                "run_id": "run-1",
            }
        )
        entry = res["written"][0]
        self.assertEqual(entry["source"], "demo")
        self.assertEqual(entry["run_id"], "run-1")
        self.assertAlmostEqual(entry["expires_at"] - entry["created_at"], 60.0)

    def test_bad_ttl_is_validation_error(self) -> None:
        res = self.service._handle_qubit_evidence(
            {"name": "q0", "entries": {"contrast": 0.5}, "ttl_s": "abc"}
        )
        self.assertFalse(res["success"])
        self.assertEqual(res["error_code"], "validation_error")

    def test_missing_name_is_validation_error(self) -> None:
        res = self.service._handle_qubit_evidence({"entries": {"contrast": 0.5}})
        self.assertFalse(res["success"])
        self.assertEqual(res["error_code"], "validation_error")

    def test_keys_are_normalized(self) -> None:
        self.service._handle_qubit_evidence(
            {"name": "q0", "entries": {"  Contrast ": 0.8, "T1_us": 44.0}}
        )
        values = EvidenceStore(path=self.path).values("q0")
        self.assertIn("contrast", values)
        self.assertIn("t1_us", values)

    # ── 进判定 ───────────────────────────────────────────────────────────

    def test_drill_moves_state(self) -> None:
        self.service._handle_qubit_evidence(
            {"name": "q0", "entries": {"freq_locked": True, "contrast": 0.85}}
        )
        state = self.service._handle_qubit_state({"name": "q0"})
        self.assertTrue(state["success"])
        self.assertEqual(state["state"]["state"], "state_discriminated")
        self.assertEqual(state["drill_evidence"]["count"], 2)

    def test_drill_reaches_characterized(self) -> None:
        self.service._handle_qubit_evidence(
            {
                "name": "q0",
                "entries": {
                    "freq_locked": True,
                    "contrast": 0.9,
                    "t1_us": 45.0,
                    "t2_star_us": 32.0,
                    "xeb_p": 0.995,
                    "xeb_gate_fidelity": 0.995,
                },
            }
        )
        state = self.service._handle_qubit_state({"name": "q0"})
        self.assertEqual(state["state"]["state"], "characterized")

    def test_drill_feeds_route(self) -> None:
        self.service._handle_qubit_evidence(
            {"name": "q0", "entries": {"freq_locked": True, "contrast": 0.85}}
        )
        decision = self.service._handle_qubit_route({"name": "q0"})
        self.assertTrue(decision["success"])
        # state_discriminated 的候选边里没有 sq.piamp 直达，这里只看它认到了 drill
        self.assertEqual(decision["drill_evidence"]["count"], 2)
        self.assertIn(decision["decision"], ("accept", "ask_llm", "reject", "hold"))

    def test_expired_drill_has_no_effect(self) -> None:
        self.service._handle_qubit_evidence(
            {"name": "q0", "entries": {"freq_locked": True}, "ttl_s": 1}
        )
        store = EvidenceStore(path=self.path)
        doc = store.load()
        doc["qubits"]["q0"]["freq_locked"]["expires_at"] = 1.0  # 早已过期
        store.save(doc["qubits"])
        state = self.service._handle_qubit_state({"name": "q0"})
        self.assertEqual(state["state"]["state"], "unknown")
        self.assertEqual(state["drill_evidence"]["count"], 0)

    # ── 冲突：真实证据优先 ───────────────────────────────────────────────

    def test_real_evidence_wins_over_drill(self) -> None:
        record = {
            "run_id": "r1",
            "qubits": ["q0"],
            "status": "completed",
            "dataset_ids": ["ds-1"],
            "metrics": {"contrast": 0.95},
            "started_at": 1.0,
            "finished_at": 2.0,
        }
        with mock.patch.object(
            runrecord.RunRecordWriter, "list_records", return_value=[record]
        ):
            self.service._handle_qubit_evidence(
                {"name": "q0", "entries": {"contrast": 0.1}}
            )
            state = self.service._handle_qubit_state({"name": "q0"})
        self.assertAlmostEqual(float(state["evidence"]["contrast"]), 0.95)
        self.assertTrue(state["conflicts"], "应有冲突记录：真实 0.95 vs 演练 0.1")
        self.assertEqual(state["conflicts"][0]["key"], "contrast")

    # ── reset ────────────────────────────────────────────────────────────

    def test_reset_one_qubit(self) -> None:
        self.service._handle_qubit_evidence({"name": "q0", "entries": {"contrast": 0.9}})
        self.service._handle_qubit_evidence({"name": "q1", "entries": {"contrast": 0.2}})
        res = self.service._handle_qubit_evidence_reset({"name": "q0"})
        self.assertTrue(res["success"])
        self.assertEqual(res["cleared"], ["q0"])
        self.assertEqual(EvidenceStore(path=self.path).values("q0"), {})
        self.assertEqual(EvidenceStore(path=self.path).values("q1")["contrast"], 0.2)

    def test_reset_all(self) -> None:
        self.service._handle_qubit_evidence({"name": "q0", "entries": {"contrast": 0.9}})
        res = self.service._handle_qubit_evidence_reset({})
        self.assertTrue(res["success"])
        self.assertEqual(sorted(res["cleared"]), ["q0"])
        self.assertEqual(EvidenceStore(path=self.path).load()["qubits"], {})

    def test_reset_then_state_back_to_unknown(self) -> None:
        self.service._handle_qubit_evidence(
            {"name": "q0", "entries": {"freq_locked": True, "contrast": 0.85}}
        )
        self.service._handle_qubit_evidence_reset({"name": "q0"})
        state = self.service._handle_qubit_state({"name": "q0"})
        self.assertEqual(state["state"]["state"], "unknown")
        self.assertEqual(state["drill_evidence"]["count"], 0)

    # ── pair scope 也吃演练证据 ──────────────────────────────────────────

    def test_pair_state_uses_drill(self) -> None:
        for qubit in ("q0", "q1"):
            self.service._handle_qubit_evidence(
                {"name": qubit, "entries": {"freq_locked": True, "contrast": 0.85}}
            )
        res = self.service._handle_qubit_pair_state({"qubits": ["q0", "q1"], "coupler": 2})
        self.assertTrue(res["success"])
        # 两个比特都到 state_discriminated（>= amp_calibrated），pair 前置应满足
        self.assertTrue(res["prerequisites"]["satisfied"])
        self.assertEqual(res["state"]["state"], "pair_ready")


if __name__ == "__main__":
    unittest.main()
