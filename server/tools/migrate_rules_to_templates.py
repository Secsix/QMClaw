#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把老 ``config/rules.json`` 迁成模板候选（BACKLOG O4 / 设计稿 §9.4）。

rules.json 是「症状 → 推荐函数」的表，**不是状态机**；这里只做兼容读取：

* 某个 ``fn`` 在 ``config/state_machine.json`` 的边表里**恰好属于一条边** → 生成一份
  模板候选（``source=manual``，``prior_success_rate=0.5``，尚无实测样本）；
* 属于多条边（如 ``sq.spectroscopy`` 同时挂在 3 条边上）或压根不在边表里 → 跳过，
  并在报告里说明原因 —— 不猜。

默认 **dry-run**（只打印，不写盘）。加 ``--write`` 才写进
``data/state_ledger/templates.json``（写盘前会与出厂模板合流，不覆盖已晋升的实例）。

用法::

    python tools/migrate_rules_to_templates.py                 # 预览
    python tools/migrate_rules_to_templates.py --write         # 落盘
    python tools/migrate_rules_to_templates.py --write --no-dry-run --prune
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.common import templates as tpl  # noqa: E402
from services.common.state_machine import load_state_machine  # noqa: E402
from services.common.template_store import TemplateStore  # noqa: E402

SERVER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RULES_PATH = os.path.join(SERVER_DIR, "config", "rules.json")


def _read_json(path):
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def main(argv=None):
    parser = argparse.ArgumentParser(description="rules.json → 模板库（默认只预览）")
    parser.add_argument("--rules", default=RULES_PATH, help="rules.json 路径")
    parser.add_argument("--write", action="store_true", help="真正写入模板库（默认 dry-run）")
    parser.add_argument("--store", default=None, help="模板库文件路径（默认 data/state_ledger/templates.json）")
    parser.add_argument("--prune", action="store_true", help="只保留本次迁移出的模板（清掉旧实例）")
    args = parser.parse_args(argv)

    if not os.path.exists(args.rules):
        print("找不到 rules.json：%s" % args.rules)
        return 2

    machine = load_state_machine()
    rules_doc = _read_json(args.rules)
    result = tpl.from_rules(rules_doc, machine)
    created = result["templates"]
    skipped = result["skipped"]

    print("规则条数：%d" % len(rules_doc.get("rules") or []))
    print("边表条数：%d" % len(machine.get("edges") or []))
    print("可迁移模板：%d" % len(created))
    for template in created:
        print("  + %-28s %-22s %s" % (template["id"], template["edge"], template["action"]["fn"]))
    if skipped:
        print("跳过：%d" % len(skipped))
        for item in skipped:
            detail = ",".join(item.get("edges") or [])
            print("  - %-16s %-22s %s%s" % (
                item.get("fn"), item.get("rule"), item.get("reason"),
                (" [%s]" % detail) if detail else "",
            ))

    if not args.write:
        print("\n（dry-run：未写盘。加 --write 落进模板库）")
        return 0
    if not created:
        print("\n没有可迁移的模板，未写盘。")
        return 0

    store = TemplateStore(path=args.store, machine=machine)
    existing = {} if args.prune else {t["id"]: t for t in store.instances()}
    for template in created:
        current = existing.get(template["id"])
        if current:
            # 已有实例：保留实测样本与晋退状态，只补结构字段
            merged = dict(template)
            merged.update({
                "observed": current.get("observed") or {"success": 0, "failure": 0},
                "consecutive_success": current.get("consecutive_success", 0),
                "consecutive_failure": current.get("consecutive_failure", 0),
                "status": current.get("status") or "normal",
                "history": current.get("history") or [],
                "last_used_at": current.get("last_used_at"),
            })
            existing[template["id"]] = tpl.normalize_template(merged)
        else:
            existing[template["id"]] = template

    ok = store.save({"templates": list(existing.values())})
    print("\n写入 %s：%s" % (store.path, "成功" if ok else "失败（已降级到临时目录）"))
    print("模板库现在共 %d 条实例" % len(store.instances()))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
