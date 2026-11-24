# -*- coding: utf-8 -*-
"""模板库与转移概率的单测（BACKLOG O4 / P3，设计稿 §9）。

覆盖三层：

* ``services/common/templates.py`` —— 纯函数（归一化、平滑率、晋退、台账折算、
  rules.json 兼容）；
* ``services/common/template_store.py`` —— 落盘器（原子写、合流、坏文件容错）；
* ``quantum_service`` 的模板端点 —— 只在 ``tests/test_state_route.py`` 之外单独
  验证 handler（不走 HTTP，服务是否重启与单测无关）。
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest

from services.common import templates as tpl
from services.common.template_store import TemplateStore


def _machine(templates=None):
    """一份最小结构表：两条边 + 一条共享 fn 的边（用于歧义判定）。"""
    return {
        "version": "test",
        "edges": [
            {
                "id": "unknown->freq_locked",
                "kind": "forward",
                "from": "unknown",
                "to": "freq_locked",
                "action": {"fn": "sq.spectroscopy", "alternatives": ["sq.spectroscopy_iq"]},
            },
            {
                "id": "freq_locked->amp_calibrated",
                "kind": "forward",
                "from": "freq_locked",
                "to": "amp_calibrated",
                "action": {"fn": "sq.piamp", "alternatives": []},
            },
            {
                "id": "amp_calibrated->state_discriminated",
                "kind": "forward",
                "from": "amp_calibrated",
                "to": "state_discriminated",
                "action": {"fn": "sq.iqraw", "alternatives": []},
            },
            {
                "id": "state_discriminated--self--recenter",
                "kind": "self",
                "from": "state_discriminated",
                "to": "state_discriminated",
                "action": {"fn": "sq.spectroscopy", "alternatives": []},
            },
        ],
        "templates": list(templates or []),
    }


def _record(status="success", datasets=None, functions=None, run_id="r1", at=1000.0):
    record = {"status": status, "run_id": run_id, "started_at": at, "finished_at": at + 1}
    if datasets is not None:
        record["dataset_ids"] = list(datasets)
    if functions is not None:
        record["functions"] = list(functions)
    return record


class TestNormalize(unittest.TestCase):
    def test_fills_structure_and_prefixes_fn(self):
        template = tpl.normalize_template({"edge": "unknown->freq_locked", "action": {"fn": "spectroscopy"}})
        self.assertEqual(template["action"]["fn"], "sq.spectroscopy")
        self.assertTrue(template["id"].startswith("T-auto-"))
        self.assertEqual(template["status"], "normal")
        self.assertEqual(template["source"], "manual")
        self.assertEqual(template["observed"], {"success": 0, "failure": 0})
        self.assertIn("success_rate", template)

    def test_id_is_stable_across_calls(self):
        raw = {"edge": "unknown->freq_locked", "action": {"fn": "sq.spectroscopy"}}
        self.assertEqual(tpl.normalize_template(raw)["id"], tpl.normalize_template(raw)["id"])

    def test_id_changes_with_params(self):
        a = tpl.normalize_template({"edge": "e", "action": {"fn": "sq.t1", "params": {"shots": 100}}})
        b = tpl.normalize_template({"edge": "e", "action": {"fn": "sq.t1", "params": {"shots": 200}}})
        self.assertNotEqual(a["id"], b["id"])

    def test_success_rate_is_recomputed_not_trusted(self):
        template = tpl.normalize_template({
            "action": {"fn": "sq.t1"},
            "observed": {"success": 4, "failure": 0},
            "success_rate": 0.01,
        })
        self.assertAlmostEqual(template["success_rate"], tpl.success_rate(template), places=6)
        self.assertGreater(template["success_rate"], 0.5)

    def test_invalid_values_are_clamped(self):
        template = tpl.normalize_template({
            "status": "bogus",
            "observed": {"success": -5, "failure": -1},
            "prior_success_rate": 5,
            "action": {},
        })
        self.assertEqual(template["status"], "normal")
        self.assertEqual(template["observed"], {"success": 0, "failure": 0})
        self.assertEqual(template["prior_success_rate"], 1.0)
        self.assertEqual(template["action"]["fn"], "")
        self.assertEqual(template["action"]["params"], {})

    def test_normalize_is_idempotent(self):
        once = tpl.normalize_template({"edge": "e", "action": {"fn": "t1"}, "observed": {"success": 1}})
        twice = tpl.normalize_template(once)
        self.assertEqual(once, twice)


class TestSuccessRate(unittest.TestCase):
    def test_prior_when_no_samples(self):
        template = {"prior_success_rate": 0.6, "observed": {"success": 0, "failure": 0}}
        self.assertAlmostEqual(tpl.success_rate(template), 0.6, places=6)

    def test_smoothing_pulls_small_samples(self):
        # (4 + 0.6) / (4 + 1)
        template = {"prior_success_rate": 0.6, "observed": {"success": 4, "failure": 0}}
        self.assertAlmostEqual(tpl.success_rate(template), 0.92, places=6)

    def test_alpha_argument_shifts_weight(self):
        template = {"prior_success_rate": 0.0, "observed": {"success": 1, "failure": 0}}
        self.assertAlmostEqual(tpl.success_rate(template, alpha=1.0), 0.5, places=6)
        self.assertAlmostEqual(tpl.success_rate(template, alpha=4.0), 0.2, places=6)

    def test_alpha_zero_falls_back_to_default(self):
        # α=0 在 total=0 时会除零，函数把它当"未指定"处理
        template = {"prior_success_rate": 0.6, "observed": {"success": 0, "failure": 0}}
        self.assertAlmostEqual(tpl.success_rate(template, alpha=0.0), 0.6, places=6)

    def test_failures_lower_rate(self):
        good = {"prior_success_rate": 0.6, "observed": {"success": 3, "failure": 0}}
        bad = {"prior_success_rate": 0.6, "observed": {"success": 3, "failure": 3}}
        self.assertGreater(tpl.success_rate(good), tpl.success_rate(bad))


class TestStatus(unittest.TestCase):
    def test_is_stable_requires_rate_and_streak(self):
        low_rate = {"success_rate": 0.5, "consecutive_success": 9}
        self.assertFalse(tpl.is_stable(low_rate))
        high_rate_no_streak = {"success_rate": 0.95, "consecutive_success": 1, "status": "normal"}
        self.assertFalse(tpl.is_stable(high_rate_no_streak))
        stable = {"success_rate": 0.95, "consecutive_success": 3, "status": "normal"}
        self.assertTrue(tpl.is_stable(stable))

    def test_promoted_status_counts_as_stable(self):
        self.assertTrue(tpl.is_stable({"status": "promoted", "success_rate": 0.9, "consecutive_success": 0}))

    def test_evaluate_status_promotes_on_streak(self):
        template = tpl.normalize_template({
            "prior_success_rate": 0.9,
            "observed": {"success": 6, "failure": 0},
            "consecutive_success": 3,
        })
        self.assertEqual(tpl.evaluate_status(template), "promoted")

    def test_evaluate_status_degrades_on_failure_streak(self):
        template = tpl.normalize_template({
            "prior_success_rate": 0.9,
            "observed": {"success": 6, "failure": 2},
            "consecutive_failure": 2,
        })
        self.assertEqual(tpl.evaluate_status(template), "degraded")

    def test_single_failure_does_not_degrade(self):
        template = tpl.normalize_template({"observed": {"success": 0, "failure": 1}})
        self.assertEqual(tpl.evaluate_status(template), "normal")

    def test_retired_is_terminal(self):
        self.assertEqual(tpl.evaluate_status({"status": "retired"}), "retired")


class TestApplyOutcome(unittest.TestCase):
    def test_does_not_mutate_input(self):
        template = tpl.normalize_template({"action": {"fn": "sq.t1"}})
        snapshot = json.dumps(template, sort_keys=True)
        tpl.apply_outcome(template, True, now=10.0)
        self.assertEqual(json.dumps(template, sort_keys=True), snapshot)

    def test_success_then_promote(self):
        template = tpl.normalize_template({"prior_success_rate": 0.9, "action": {"fn": "sq.t1"}})
        for index in range(3):
            template = tpl.apply_outcome(template, True, now=10.0 + index, run_id="r%d" % index)
        self.assertEqual(template["status"], "promoted")
        self.assertEqual(template["last_transition"], "normal->promoted")
        self.assertEqual(template["consecutive_success"], 3)
        self.assertEqual(template["last_run_id"], "r2")

    def test_failure_streak_degrades(self):
        template = tpl.normalize_template({"prior_success_rate": 0.9, "observed": {"success": 3}})
        template = tpl.apply_outcome(template, False, now=1.0)
        template = tpl.apply_outcome(template, False, now=2.0)
        self.assertEqual(template["status"], "degraded")
        self.assertEqual(template["last_transition"], "normal->degraded")
        self.assertEqual(template["consecutive_failure"], 2)

    def test_history_is_bounded(self):
        template = tpl.normalize_template({"action": {"fn": "sq.t1"}})
        for index in range(tpl.HISTORY_LIMIT + 5):
            template = tpl.apply_outcome(template, True, now=float(index))
        self.assertEqual(len(template["history"]), tpl.HISTORY_LIMIT)

    def test_retired_ignores_outcomes(self):
        template = tpl.retire(tpl.normalize_template({"action": {"fn": "sq.t1"}}), "手动出库")
        updated = tpl.apply_outcome(template, True, now=5.0)
        self.assertEqual(updated["status"], "retired")
        self.assertEqual(updated["observed"], {"success": 0, "failure": 0})
        self.assertEqual(updated["last_transition"], "")


class TestRetireAndRank(unittest.TestCase):
    def test_retire_records_reason(self):
        template = tpl.retire(tpl.normalize_template({"action": {"fn": "sq.t1"}}), "长期不用", now=42.0)
        self.assertEqual(template["status"], "retired")
        self.assertEqual(template["retired_reason"], "长期不用")
        self.assertEqual(template["retired_at"], 42.0)
        self.assertEqual(template["last_transition"], "normal->retired")

    def test_retire_stale_drops_unknown_edge(self):
        items = [
            tpl.normalize_template({"edge": "unknown->freq_locked", "action": {"fn": "sq.t1"}}),
            tpl.normalize_template({"edge": "gone->away", "action": {"fn": "sq.t2"}}),
        ]
        out = tpl.retire_stale(items, machine=_machine())
        statuses = {t["edge"]: t["status"] for t in out}
        self.assertEqual(statuses["gone->away"], "retired")
        self.assertEqual(statuses["unknown->freq_locked"], "normal")

    def test_retire_stale_idle(self):
        fresh = tpl.normalize_template({"edge": "unknown->freq_locked", "action": {"fn": "sq.t1"}, "last_used_at": 1000.0})
        out = tpl.retire_stale([fresh], max_idle_seconds=10.0, now=2000.0)
        self.assertEqual(out[0]["status"], "retired")

    def test_rank_sorts_and_drops_retired(self):
        a = tpl.normalize_template({"edge": "e", "id": "A", "action": {"fn": "sq.a"}, "observed": {"success": 0, "failure": 5}})
        b = tpl.normalize_template({"edge": "e", "id": "B", "action": {"fn": "sq.b"}, "observed": {"success": 5, "failure": 0}})
        c = tpl.retire(tpl.normalize_template({"edge": "e", "id": "C", "action": {"fn": "sq.c"}}))
        ranked = tpl.rank([a, b, c])
        self.assertEqual([t["id"] for t in ranked], ["B", "A"])

    def test_rank_filters_by_edge(self):
        a = tpl.normalize_template({"edge": "e1", "id": "A", "action": {"fn": "sq.a"}})
        b = tpl.normalize_template({"edge": "e2", "id": "B", "action": {"fn": "sq.b"}})
        self.assertEqual([t["id"] for t in tpl.rank([a, b], edge="e2")], ["B"])


class TestMachineMerge(unittest.TestCase):
    def test_templates_from_machine_skips_retired(self):
        machine = _machine(templates=[
            {"id": "T1", "edge": "unknown->freq_locked", "action": {"fn": "sq.spectroscopy"}},
            {"id": "T2", "edge": "freq_locked->amp_calibrated", "action": {"fn": "sq.piamp"}, "status": "retired"},
        ])
        self.assertEqual([t["id"] for t in tpl.templates_from_machine(machine)], ["T1"])

    def test_merge_overlay_wins_and_keeps_order(self):
        base = [tpl.normalize_template({"id": "T1", "action": {"fn": "sq.a"}}),
                tpl.normalize_template({"id": "T2", "action": {"fn": "sq.b"}})]
        overlay = [tpl.normalize_template({"id": "T2", "action": {"fn": "sq.b"}, "observed": {"success": 9}}),
                   tpl.normalize_template({"id": "T3", "action": {"fn": "sq.c"}})]
        merged = tpl.merge_templates(base, overlay)
        self.assertEqual([t["id"] for t in merged], ["T1", "T2", "T3"])
        self.assertEqual(merged[1]["observed"]["success"], 9)

    def test_stats_counts(self):
        items = [
            tpl.normalize_template({"edge": "e1", "action": {"fn": "sq.a"}, "status": "promoted"}),
            tpl.normalize_template({"edge": "e1", "action": {"fn": "sq.b"}}),
            tpl.retire(tpl.normalize_template({"edge": "e2", "action": {"fn": "sq.c"}})),
        ]
        result = tpl.stats(items)
        self.assertEqual(result["count"], 3)
        self.assertEqual(result["by_status"]["promoted"], 1)
        self.assertEqual(result["by_status"]["retired"], 1)
        self.assertEqual(result["promoted"], 1)


class TestRecords(unittest.TestCase):
    def test_outcome_mapping(self):
        self.assertFalse(tpl.outcome_from_record(_record(status="failed")))
        self.assertFalse(tpl.outcome_from_record(_record(status="timeout")))
        self.assertTrue(tpl.outcome_from_record(_record(status="qiskit_completed", datasets=["d1"])))
        self.assertIsNone(tpl.outcome_from_record(_record(status="qiskit_completed")))
        self.assertIsNone(tpl.outcome_from_record(_record(status="offline_no_simulator")))

    def test_infer_skips_ambiguous_functions(self):
        # sq.spectroscopy 同时属于两条边 → 必须靠 len(matches) 判歧义
        items = [
            tpl.normalize_template({"id": "A", "edge": "unknown->freq_locked", "action": {"fn": "sq.spectroscopy"}}),
            tpl.normalize_template({"id": "B", "edge": "state_discriminated--self--recenter", "action": {"fn": "sq.spectroscopy"}}),
        ]
        result = tpl.infer_outcomes(items, [_record(datasets=["d1"], functions=["spectroscopy"])])
        self.assertEqual(result["applied"], [])
        self.assertEqual(result["skipped"][0]["reason"], "ambiguous_functions")

    def test_infer_records_no_template(self):
        items = [tpl.normalize_template({"id": "A", "edge": "e", "action": {"fn": "sq.piamp"}})]
        result = tpl.infer_outcomes(items, [_record(datasets=["d1"], functions=["t1"])])
        self.assertEqual(result["skipped"][0]["reason"], "no_template")

    def test_infer_marks_success_without_dataset(self):
        items = [tpl.normalize_template({"id": "A", "edge": "e", "action": {"fn": "sq.t1"}})]
        result = tpl.infer_outcomes(items, [_record(status="qiskit_completed", functions=["t1"])])
        self.assertEqual(result["skipped"][0]["reason"], "success_without_dataset")

    def test_infer_no_functions(self):
        result = tpl.infer_outcomes([], [_record(datasets=["d1"], functions=[])])
        self.assertEqual(result["skipped"][0]["reason"], "no_functions")

    def test_infer_applies_unique_match(self):
        items = [tpl.normalize_template({"id": "A", "edge": "e", "action": {"fn": "sq.t1"}})]
        result = tpl.infer_outcomes(items, [_record(datasets=["d1"], functions=["t1"])])
        self.assertEqual(len(result["applied"]), 1)
        self.assertTrue(result["applied"][0]["success"])

    def test_update_from_records_promotes(self):
        items = [tpl.normalize_template({
            "id": "A", "edge": "e", "action": {"fn": "sq.t1"},
            "prior_success_rate": 0.9, "consecutive_success_for_promote": 3,
        })]
        records = [
            _record(datasets=["d1"], functions=["t1"], run_id="r%d" % i, at=1000.0 + i)
            for i in range(3)
        ]
        result = tpl.update_from_records(items, records)
        self.assertEqual(len(result["applied"]), 3)
        self.assertEqual(result["templates"][0]["status"], "promoted")
        self.assertEqual(len(result["transitions"]), 1)
        self.assertEqual(result["transitions"][0]["transition"], "normal->promoted")


class TestFromRules(unittest.TestCase):
    RULES = {
        "rules": [
            {"name": "pi_amp_uncertain", "condition": "pi_amp_uncertain", "symptom": "pi 幅不稳",
             "recommendations": [{"fn": "sq.piamp", "reason": "重扫 pi 幅", "priority": 1}]},
            {"name": "snr_low", "condition": "snr_low", "symptom": "SNR 低",
             "recommendations": [{"fn": "sq.spectroscopy", "reason": "重扫谱", "priority": 1}]},
            {"name": "unknown_fn", "condition": "x", "symptom": "y",
             "recommendations": [{"fn": "sq.nonexistent", "reason": "z", "priority": 1}]},
        ]
    }

    def test_unique_edge_creates_template(self):
        result = tpl.from_rules(self.RULES, _machine())
        fns = [t["action"]["fn"] for t in result["templates"]]
        self.assertIn("sq.piamp", fns)
        template = [t for t in result["templates"] if t["action"]["fn"] == "sq.piamp"][0]
        self.assertEqual(template["edge"], "freq_locked->amp_calibrated")
        self.assertEqual(template["source"], "manual")
        self.assertTrue(template["id"].startswith("T-rule-"))

    def test_ambiguous_and_missing_fns_are_skipped(self):
        result = tpl.from_rules(self.RULES, _machine())
        reasons = {(s["fn"], s["reason"]) for s in result["skipped"]}
        self.assertIn(("sq.spectroscopy", "edge_ambiguous"), reasons)
        self.assertIn(("sq.nonexistent", "edge_not_found"), reasons)
        self.assertNotIn("sq.spectroscopy", [t["action"]["fn"] for t in result["templates"]])

    def test_empty_rules(self):
        self.assertEqual(tpl.from_rules({}, _machine()), {"templates": [], "skipped": []})

    def test_same_edge_fn_is_deduped(self):
        rules = {
            "rules": [
                {"name": "t1_short", "recommendations": [{"fn": "sq.t1", "priority": 1}]},
                {"name": "t2_short", "recommendations": [{"fn": "t1", "priority": 1}]},
            ]
        }
        machine = _machine(templates=[])
        machine["edges"] = [{
            "id": "state_discriminated->coherence_measured",
            "from": "state_discriminated",
            "to": "coherence_measured",
            "action": {"fn": "sq.t1", "alternatives": []},
        }]
        result = tpl.from_rules(rules, machine)
        self.assertEqual(len(result["templates"]), 1)
        self.assertEqual(result["skipped"][0]["reason"], "duplicate_edge_fn")


class TestStoreFeedsRouter(unittest.TestCase):
    """O4 的真正意义：运行期实例能被 Router 看见（否则永远走 G3）。"""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="qmclaw-tpl-router-")
        self.store = TemplateStore(path=os.path.join(self.dir, "templates.json"), machine=_machine())

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _record(self, state="freq_locked"):
        return {
            "qubit": "q0",
            "scope": {"type": "qubit", "qubits": ["q0"], "coupler": None},
            "state": state,
            "state_index": 1,
            "state_version": "1.0.0",
            "criteria_version": "1.0.0",
            "evidence": {},
            "conflicts": [],
            "reasons": [],
            "missing": [],
            "decided_at": 1000.0,
            "expires_at": None,
        }

    def _route(self, machine):
        from services.common.router import route

        return route(
            self._record(),
            ["sq.piamp"],
            {"version": "1.0.0"},
            {},
            {"actuation": {"allow_execute": True, "allow_set_params": True}, "now": 2000.0,
             "machine": machine},
            machine,
        )

    def test_no_instance_falls_to_g3(self):
        self.assertEqual(self._route(_machine())["branch"], "G3")

    def test_promoted_instance_reaches_g1(self):
        self.store.upsert({
            "id": "T1",
            "edge": "freq_locked->amp_calibrated",
            "action": {"fn": "sq.piamp", "params": {}},
            "prior_success_rate": 0.9,
            "observed": {"success": 6, "failure": 0},
            "consecutive_success": 3,
            "status": "promoted",
        })
        machine = _machine()
        machine["templates"] = self.store.merged(machine)
        decision = self._route(machine)
        self.assertEqual(decision["branch"], "G1")
        self.assertEqual(decision["decision"], "accept")
        self.assertEqual(decision["chosen_action"]["fn"], "sq.piamp")
        self.assertEqual(decision["chosen_action"]["edge_id"], "freq_locked->amp_calibrated")

    def test_retired_instance_is_invisible(self):
        self.store.upsert({
            "id": "T1",
            "edge": "freq_locked->amp_calibrated",
            "action": {"fn": "sq.piamp", "params": {}},
            "observed": {"success": 6, "failure": 0},
            "consecutive_success": 3,
            "status": "promoted",
        })
        self.store.retire("T1", "测试出库")
        machine = _machine()
        machine["templates"] = self.store.merged(machine)
        self.assertEqual(self._route(machine)["branch"], "G3")


class TestTemplateStore(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="qmclaw-tpl-")
        self.path = os.path.join(self.dir, "templates.json")
        self.store = TemplateStore(path=self.path, machine=_machine())

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_empty_store(self):
        self.assertEqual(self.store.load()["templates"], [])
        self.assertEqual(self.store.merged(), [])
        self.assertEqual(self.store.summary()["effective"], 0)

    def test_upsert_persists(self):
        stored = self.store.upsert({"edge": "freq_locked->amp_calibrated", "action": {"fn": "sq.piamp"}})
        self.assertTrue(os.path.exists(self.path))
        reread = TemplateStore(path=self.path, machine=_machine())
        self.assertEqual([t["id"] for t in reread.instances()], [stored["id"]])

    def test_upsert_replaces_same_id(self):
        self.store.upsert({"id": "T1", "edge": "e", "action": {"fn": "sq.t1"}, "observed": {"success": 1}})
        self.store.upsert({"id": "T1", "edge": "e", "action": {"fn": "sq.t1"}, "observed": {"success": 5}})
        self.assertEqual(len(self.store.instances()), 1)
        self.assertEqual(self.store.instances()[0]["observed"]["success"], 5)

    def test_record_outcome_by_id_and_by_edge(self):
        self.store.upsert({"id": "T1", "edge": "freq_locked->amp_calibrated", "action": {"fn": "sq.piamp"}})
        by_id = self.store.record_outcome(True, template_id="T1")
        self.assertTrue(by_id["ok"])
        self.assertEqual(by_id["template"]["observed"]["success"], 1)
        by_edge = self.store.record_outcome(True, edge="freq_locked->amp_calibrated", fn="piamp")
        self.assertTrue(by_edge["ok"])
        self.assertEqual(by_edge["template"]["observed"]["success"], 2)

    def test_record_outcome_missing_template(self):
        result = self.store.record_outcome(True, template_id="nope")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "template_not_found")

    def test_record_outcome_ambiguous_match(self):
        self.store.upsert({"id": "A", "edge": "e", "action": {"fn": "sq.t1"}})
        self.store.upsert({"id": "B", "edge": "e", "action": {"fn": "sq.t1"}})
        self.assertFalse(self.store.record_outcome(True, edge="e", fn="sq.t1")["ok"])

    def test_merged_includes_config_templates(self):
        store = TemplateStore(
            path=self.path,
            machine=_machine(templates=[{"id": "CFG", "edge": "unknown->freq_locked", "action": {"fn": "sq.spectroscopy"}}]),
        )
        self.assertEqual([t["id"] for t in store.merged()], ["CFG"])
        store.upsert({"id": "CFG", "edge": "unknown->freq_locked", "action": {"fn": "sq.spectroscopy"}, "observed": {"success": 3}})
        merged = store.merged()
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["observed"]["success"], 3)

    def test_merged_drops_templates_for_removed_edges(self):
        self.store.upsert({"id": "GONE", "edge": "nope->nowhere", "action": {"fn": "sq.t1"}})
        self.assertEqual(self.store.merged(), [])

    def test_refresh_from_records(self):
        self.store.upsert({
            "id": "T1", "edge": "freq_locked->amp_calibrated", "action": {"fn": "sq.piamp"},
            "prior_success_rate": 0.9,
        })
        records = [
            _record(datasets=["d1"], functions=["piamp"], run_id="r%d" % i, at=1000.0 + i)
            for i in range(3)
        ]
        result = self.store.refresh_from_records(records=records)
        self.assertTrue(result["ok"])
        self.assertEqual(result["applied"], 3)
        self.assertEqual(self.store.instances()[0]["status"], "promoted")

    def test_retire_and_reset(self):
        self.store.upsert({"id": "T1", "edge": "freq_locked->amp_calibrated", "action": {"fn": "sq.piamp"}})
        self.assertEqual(self.store.retire("T1", "测试出库")["template"]["status"], "retired")
        self.assertFalse(self.store.retire("nope")["ok"])
        self.assertTrue(self.store.reset()["ok"])
        self.assertEqual(self.store.instances(), [])

    def test_corrupt_file_is_tolerated(self):
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write("{not json")
        self.assertEqual(self.store.instances(), [])
        self.assertTrue(self.store.upsert({"edge": "e", "action": {"fn": "sq.t1"}})["id"])

    def test_saved_file_is_valid_json(self):
        self.store.upsert({"edge": "e", "action": {"fn": "sq.t1"}})
        with open(self.path, encoding="utf-8") as handle:
            data = json.load(handle)
        self.assertEqual(data["version"], 1)
        self.assertEqual(len(data["templates"]), 1)


if __name__ == "__main__":
    unittest.main()
