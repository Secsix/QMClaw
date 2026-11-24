"""services/common/template_store.py — 模板运行期实例的落盘器（BACKLOG O4 · P3）。

职责边界（设计稿 §9.1）：

* ``config/state_machine.json`` 的 ``templates`` 段 = **结构（出厂模板）**，人写；
* ``data/state_ledger/templates.json`` = **运行期实例**（实测成功率、连续成功/失败、
  晋退状态、近期结果），机器写；
* 本模块只做这两者的合流与实例的读写，判定逻辑全在 ``services/common/templates.py``
  （纯函数）。写盘失败不抛：模板库坏了不能把测控链路带崩。
"""

from __future__ import annotations

import copy
import json
import os
import tempfile
import threading
import time
from typing import Any, Dict, Iterable, List, Optional

from services.common import templates as tpl

STORE_VERSION = 1
STORE_FILENAME = "templates.json"


def default_store_dir() -> str:
    """``<server>/data/state_ledger``（与 ``data/run-records`` 同级）。"""
    server_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(server_dir, "data", "state_ledger")


class TemplateStore:
    """模板实例的读写（线程安全；目录不可写时降级到临时目录，不抛异常）。"""

    def __init__(self, path: Optional[str] = None, machine: Optional[Dict[str, Any]] = None):
        self._lock = threading.Lock()
        self._path = path or os.path.join(default_store_dir(), STORE_FILENAME)
        self._machine = machine
        self._fallback_dir: Optional[str] = None

    # ── 路径 / 结构表 ──────────────────────────────────────────────────────

    @property
    def path(self) -> str:
        return self._path

    def _structure(self) -> Dict[str, Any]:
        if self._machine is not None:
            return dict(self._machine)
        try:
            from services.common.state_machine import load_state_machine

            return load_state_machine()
        except Exception:
            return {"edges": [], "templates": []}

    # ── 读写 ──────────────────────────────────────────────────────────────

    def _read_path(self) -> Optional[str]:
        if os.path.exists(self._path):
            return self._path
        if self._fallback_dir:
            alt = os.path.join(self._fallback_dir, STORE_FILENAME)
            if os.path.exists(alt):
                return alt
        return None

    def load(self) -> Dict[str, Any]:
        """读实例文件；不存在 / 坏了都返回空表（不抛）。"""
        target = self._read_path()
        if not target:
            return {"version": STORE_VERSION, "updated": None, "templates": []}
        try:
            with open(target, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except Exception:
            return {"version": STORE_VERSION, "updated": None, "templates": []}
        if not isinstance(data, dict):
            return {"version": STORE_VERSION, "updated": None, "templates": []}
        data.setdefault("version", STORE_VERSION)
        if not isinstance(data.get("templates"), list):
            data["templates"] = []
        return data

    def save(self, doc: Dict[str, Any]) -> bool:
        """原子写（先写临时文件再 replace）；失败返回 False 并降级到临时目录。"""
        payload = {
            "version": STORE_VERSION,
            "updated": float(time.time()),
            "templates": [dict(t) for t in (doc.get("templates") or [])],
        }
        with self._lock:
            for directory in (os.path.dirname(self._path), self._fallback_dir):
                if not directory:
                    continue
                try:
                    os.makedirs(directory, exist_ok=True)
                    handle = tempfile.NamedTemporaryFile(
                        "w", encoding="utf-8", dir=directory, delete=False, suffix=".tmp"
                    )
                    try:
                        json.dump(payload, handle, ensure_ascii=False, indent=2)
                        handle.write("\n")
                        handle.flush()
                        os.fsync(handle.fileno())
                    finally:
                        handle.close()
                    target = os.path.join(directory, STORE_FILENAME)
                    os.replace(handle.name, target)
                    if directory != os.path.dirname(self._path):
                        self._fallback_dir = directory
                    return True
                except Exception:
                    continue
            if self._fallback_dir is None:
                self._fallback_dir = tempfile.mkdtemp(prefix="qmclaw-templates-")
        return False

    # ── 合流 ──────────────────────────────────────────────────────────────

    def instances(self) -> List[Dict[str, Any]]:
        """运行期实例（已归一化，含重算后的 success_rate）。"""
        return [tpl.normalize_template(raw) for raw in (self.load().get("templates") or [])]

    def merged(self, machine: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """出厂模板 + 运行期实例（同 id 以实例为准），并把过期的标成 retired。"""
        structure = dict(machine) if machine is not None else self._structure()
        base = tpl.templates_from_machine(structure)
        merged = tpl.merge_templates(base, self.instances())
        pruned = tpl.retire_stale(merged, machine=structure)
        return [t for t in pruned if t.get("status") != "retired"]

    # ── 写入 ──────────────────────────────────────────────────────────────

    def _save_instances(self, items: Iterable[Dict[str, Any]]) -> bool:
        return self.save({"templates": [tpl.normalize_template(t) for t in items]})

    def upsert(self, template: Any) -> Dict[str, Any]:
        """新增/更新一份实例（按 id 覆盖）。"""
        candidate = tpl.normalize_template(template)
        items = self.instances()
        replaced = False
        for index, item in enumerate(items):
            if item["id"] == candidate["id"]:
                items[index] = candidate
                replaced = True
                break
        if not replaced:
            items.append(candidate)
        self._save_instances(items)
        return candidate

    def record_outcome(
        self,
        success: bool,
        template_id: Optional[str] = None,
        edge: Optional[str] = None,
        fn: Optional[str] = None,
        run_id: Optional[str] = None,
        now: Optional[float] = None,
        alpha: Optional[float] = None,
    ) -> Dict[str, Any]:
        """记一次实测结果（按 id 或「边 + 动作」定位模板）。"""
        items = self.merged()
        target = self._locate(items, template_id=template_id, edge=edge, fn=fn)
        if target is None:
            return {
                "ok": False,
                "error": "template_not_found",
                "detail": "找不到模板（id=%s / edge=%s / fn=%s）" % (template_id, edge, fn),
                "templates": [t["id"] for t in items],
            }
        updated = tpl.apply_outcome(target, success, now=now, run_id=run_id, alpha=alpha)
        items = [updated if t["id"] == updated["id"] else t for t in items]
        self._save_instances(items)
        return {
            "ok": True,
            "template": updated,
            "promoted": updated["status"] == "promoted",
            "degraded": updated["status"] == "degraded",
            "transition": updated.get("last_transition") or "",
        }

    def _locate(
        self,
        items: List[Dict[str, Any]],
        template_id: Optional[str] = None,
        edge: Optional[str] = None,
        fn: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        if template_id:
            for item in items:
                if item["id"] == str(template_id):
                    return item
            return None
        wanted_fn = tpl.normalize_fn(fn) if fn else ""
        matches = [
            item for item in items
            if (not edge or str(item.get("edge")) == str(edge))
            and (not wanted_fn or tpl.normalize_fn((item.get("action") or {}).get("fn")) == wanted_fn)
        ]
        if len(matches) == 1:
            return matches[0]
        return None

    def refresh_from_records(
        self,
        limit: int = 500,
        records: Optional[Iterable[Dict[str, Any]]] = None,
        alpha: Optional[float] = None,
    ) -> Dict[str, Any]:
        """从 RunRecord 台账批量更新概率（§9.2）。"""
        if records is None:
            try:
                from services.common.runrecord import RunRecordWriter

                records = RunRecordWriter().list_records(limit=max(1, int(limit)))
            except Exception as exc:
                return {"ok": False, "error": "ledger_unavailable", "detail": str(exc)}
        items = self.merged()
        result = tpl.update_from_records(items, records, alpha=alpha)
        self._save_instances(result["templates"])
        return {
            "ok": True,
            "applied": len(result["applied"]),
            "skipped": len(result["skipped"]),
            "transitions": result["transitions"],
            "skipped_detail": result["skipped"][:20],
            "templates": result["templates"],
        }

    def retire(self, template_id: str, reason: str = "") -> Dict[str, Any]:
        items = self.merged()
        found = None
        for item in items:
            if item["id"] == str(template_id):
                found = tpl.retire(item, reason=reason)
                break
        if found is None:
            return {"ok": False, "error": "template_not_found", "template_id": template_id}
        items = [found if t["id"] == found["id"] else t for t in items]
        self._save_instances(items)
        return {"ok": True, "template": found}

    def reset(self) -> Dict[str, Any]:
        """清空运行期实例（出厂模板仍在 config 里，不受影响）。"""
        ok = self.save({"templates": []})
        return {"ok": ok}

    def summary(self) -> Dict[str, Any]:
        items = self.merged()
        return {
            "path": self.path,
            "instances": len(self.instances()),
            "effective": len(items),
            "stats": tpl.stats(items),
        }
