"""services/common/evidence_store.py — 演练证据（manual evidence）的落盘器。

用途（BACKLOG O8 / 控制台演示）：

模拟后端（qiskit / offline_sim）产出的 metrics 键与状态机认的键不同，而且
``evidence_from_records`` 只认「成功且带 dataset_ids」的台账记录 —— 模拟实验两条都不满足，
所以只跑实验永远推不动状态。演示需要一条**可持久化**的「演练证据」通道：

* ``data/state_ledger/evidence.json`` = 演练证据（人/脚本写，``source="manual"``）；
* 真正的证据链仍然是执行台账（``data/run-records``），本模块**不参与**它的读写；
* 合流时**真实证据优先**，演练证据只补缺（见 ``state_evidence.merge_drill_evidence``）；
* 每条证据带 ``expires_at``（默认 8 小时），到期自动失效 —— 长期污染状态是要付代价的。

纯 IO 模块，判定逻辑一律在 ``services/common/qubit_state.py``（红线：不改）。
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from typing import Any, Dict, Iterable, List, Optional

STORE_VERSION = 1
STORE_FILENAME = "evidence.json"
DEFAULT_TTL_S = 8 * 3600.0
DEFAULT_SOURCE = "manual"


def default_store_dir() -> str:
    """``<server>/data/state_ledger``（与 ``templates.json`` 同目录）。"""
    server_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(server_dir, "data", "state_ledger")


def _now(now: Optional[float] = None) -> float:
    return float(time.time() if now is None else now)


def normalize_entry(
    key: str,
    value: Any,
    source: Optional[str] = None,
    run_id: Optional[str] = None,
    note: str = "",
    ttl_s: Optional[float] = None,
    now: Optional[float] = None,
    created_at: Optional[float] = None,
) -> Dict[str, Any]:
    """归一化一条证据（值原样保留：bool / int / float / str）。

    key 一律收成 ``strip().lower()``：状态机认的证据键全是小写
    （``contrast`` / ``t1_us`` / ``xeb_gate_fidelity`` …），调用方写 ``T1_us``
    或带空格时不该被当成另一条无关键。
    """
    created = _now(now) if created_at is None else float(created_at)
    ttl = DEFAULT_TTL_S if ttl_s is None else float(ttl_s)
    expires = None if ttl <= 0 else created + ttl
    return {
        "key": str(key).strip().lower(),
        "value": value,
        "source": str(source or DEFAULT_SOURCE),
        "run_id": run_id,
        "note": str(note or ""),
        "created_at": created,
        "expires_at": expires,
    }


def is_active(entry: Any, now: Optional[float] = None) -> bool:
    """未过期（``expires_at`` 为空 = 永不过期）。"""
    if not isinstance(entry, dict):
        return False
    expires = entry.get("expires_at")
    if expires is None:
        return True
    try:
        return float(expires) > _now(now)
    except (TypeError, ValueError):
        return True


def classify_value(value: Any) -> str:
    """值的类型标签（bool 必须先判，Python 里 bool 是 int 的子类）。"""
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    return "other"


class EvidenceStore:
    """演练证据的读写（线程安全；目录不可写时降级到临时目录，不抛异常）。"""

    def __init__(self, path: Optional[str] = None):
        self._lock = threading.Lock()
        self._path = path or os.path.join(default_store_dir(), STORE_FILENAME)
        self._fallback_dir: Optional[str] = None

    # ── 路径 ──────────────────────────────────────────────────────────────

    @property
    def path(self) -> str:
        return self._path

    def _read_path(self) -> Optional[str]:
        if os.path.exists(self._path):
            return self._path
        if self._fallback_dir:
            alt = os.path.join(self._fallback_dir, STORE_FILENAME)
            if os.path.exists(alt):
                return alt
        return None

    # ── 读写 ──────────────────────────────────────────────────────────────

    def load(self) -> Dict[str, Any]:
        """读全表；不存在 / 坏了都返回空表（不抛）。"""
        empty = {"version": STORE_VERSION, "updated": None, "qubits": {}}
        target = self._read_path()
        if not target:
            return empty
        try:
            with open(target, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except Exception:
            return empty
        if not isinstance(data, dict):
            return empty
        data.setdefault("version", STORE_VERSION)
        qubits = data.get("qubits")
        if not isinstance(qubits, dict):
            data["qubits"] = {}
        return data

    def _qubits(self) -> Dict[str, Dict[str, Any]]:
        return {str(name): dict(entries or {}) for name, entries in (self.load().get("qubits") or {}).items()}

    def save(self, qubits: Dict[str, Dict[str, Any]]) -> bool:
        """原子写（临时文件 + fsync + os.replace）；失败降级到临时目录。"""
        payload = {
            "version": STORE_VERSION,
            "updated": float(time.time()),
            "qubits": {str(name): dict(entries or {}) for name, entries in (qubits or {}).items()},
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
                self._fallback_dir = tempfile.mkdtemp(prefix="qmclaw-evidence-")
        return False

    # ── 读 ────────────────────────────────────────────────────────────────

    def entries(
        self,
        qubit: str,
        now: Optional[float] = None,
        include_expired: bool = False,
    ) -> List[Dict[str, Any]]:
        """某个比特的有效证据（按 key 排序；``include_expired`` 用于审计）。"""
        raw = (self._qubits().get(str(qubit)) or {})
        items: List[Dict[str, Any]] = []
        for key, entry in raw.items():
            if not isinstance(entry, dict):
                continue
            if not include_expired and not is_active(entry, now):
                continue
            item = dict(entry)
            item.setdefault("key", key)
            item["active"] = is_active(item, now)
            items.append(item)
        items.sort(key=lambda e: str(e.get("key")))
        return items

    def values(self, qubit: str, now: Optional[float] = None) -> Dict[str, Any]:
        """``{key: value}``（过期的自动剔除）。"""
        return {str(e["key"]): e.get("value") for e in self.entries(qubit, now=now)}

    def sources(self, qubit: str, now: Optional[float] = None) -> Dict[str, Any]:
        """``{key: {source, run_id, note, created_at, expires_at}}``。"""
        out: Dict[str, Any] = {}
        for entry in self.entries(qubit, now=now):
            out[str(entry["key"])] = {
                "source": entry.get("source") or DEFAULT_SOURCE,
                "run_id": entry.get("run_id"),
                "note": entry.get("note") or "",
                "created_at": entry.get("created_at"),
                "expires_at": entry.get("expires_at"),
            }
        return out

    def summary(self, qubit: Optional[str] = None, now: Optional[float] = None) -> Dict[str, Any]:
        qubits = self._qubits()
        if qubit:
            active = self.entries(qubit, now=now)
            return {
                "path": self.path,
                "qubit": str(qubit),
                "count": len(active),
                "keys": [e["key"] for e in active],
                "expired": len(self.entries(qubit, now=now, include_expired=True)) - len(active),
            }
        per: Dict[str, Any] = {}
        for name in sorted(qubits):
            active = self.entries(name, now=now)
            expired = len(self.entries(name, now=now, include_expired=True)) - len(active)
            if active or expired:
                per[name] = {"count": len(active), "keys": [e["key"] for e in active], "expired": expired}
        return {"path": self.path, "qubits": per, "total": sum(v["count"] for v in per.values())}

    # ── 写 ────────────────────────────────────────────────────────────────

    def set_entry(
        self,
        qubit: str,
        key: str,
        value: Any,
        source: Optional[str] = None,
        run_id: Optional[str] = None,
        note: str = "",
        ttl_s: Optional[float] = None,
        now: Optional[float] = None,
    ) -> Dict[str, Any]:
        """写入/覆盖一条证据，返回归一化后的 entry。"""
        return self.merge(
            qubit,
            {str(key): value},
            source=source,
            run_id=run_id,
            note=note,
            ttl_s=ttl_s,
            now=now,
        )["entries"][0]

    def merge(
        self,
        qubit: str,
        values: Dict[str, Any],
        source: Optional[str] = None,
        run_id: Optional[str] = None,
        note: str = "",
        ttl_s: Optional[float] = None,
        now: Optional[float] = None,
    ) -> Dict[str, Any]:
        """批量写入（同 key 覆盖；``None`` 值跳过）。"""
        if not isinstance(values, dict) or not values:
            return {"ok": False, "error": "empty_values", "entries": []}
        name = str(qubit or "").strip()
        if not name:
            return {"ok": False, "error": "missing_qubit", "entries": []}
        created = _now(now)
        qubits = self._qubits()
        bucket = dict(qubits.get(name) or {})
        written: List[Dict[str, Any]] = []
        for key, value in values.items():
            if value is None:
                continue
            entry = normalize_entry(
                key,
                value,
                source=source,
                run_id=run_id,
                note=note,
                ttl_s=ttl_s,
                now=created,
            )
            bucket[str(entry["key"])] = entry
            written.append(entry)
        qubits[name] = bucket
        ok = self.save(qubits)
        return {"ok": ok, "qubit": name, "entries": written, "count": len(written)}

    def remove(self, qubit: str, keys: Optional[Iterable[str]] = None) -> Dict[str, Any]:
        """删某比特的若干 key（``keys`` 为空 = 删该比特全部）。"""
        name = str(qubit or "").strip()
        qubits = self._qubits()
        removed: List[str] = []
        if keys:
            bucket = dict(qubits.get(name) or {})
            for key in keys:
                if str(key) in bucket:
                    bucket.pop(str(key))
                    removed.append(str(key))
            if bucket:
                qubits[name] = bucket
            else:
                qubits.pop(name, None)
        else:
            removed = sorted((qubits.pop(name) or {}).keys())
        ok = self.save(qubits) if (removed or not keys) else True
        return {"ok": ok, "qubit": name, "removed": removed}

    def reset(self, qubit: Optional[str] = None) -> Dict[str, Any]:
        """清空（给了 qubit 只清它，否则清全表）。"""
        if qubit:
            result = self.remove(str(qubit))
            return {"ok": result["ok"], "cleared": [str(qubit)], "removed": result["removed"]}
        names = sorted(self._qubits().keys())
        ok = self.save({})
        return {"ok": ok, "cleared": names}
