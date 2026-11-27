"""实验目录：kind 的唯一真源。

来源（按优先级合并）：

1. ``server/config/experiment_configs.json`` —— 实验室登记过的 14 个实验，
   带 ``function`` / ``qubits`` / ``metricsToExtract`` / ``defaultCommand``（后者是
   vendor 代码样例，**只在本服务内部使用，不外发给技能**）；
2. 上游真机词表 ``SQ_NODES``（`vendor/OpenQWF/adapters/lqcs.py`）—— 补齐没登记的
   ``pidf`` / ``spectroscopy_auto`` / ``ramsey_t2`` 等；
3. qiskit 词表（`services/quantum_service/qiskit_experiments.supported_experiments()`）
   —— 决定 ``backends.qiskit`` 真假。

每个 kind 的元数据：

* ``fn``：vendor 函数名（``sq.t1`` / ``cz.dcz``），只在本服务内部流转；
* ``params``：参数 schema（name → {type, default, required, unit, note}）；
* ``backends``：``{lqcs, qiskit, offline_sim}`` 支持矩阵；
* ``produces``：这次实验能产出哪些原始指标（``metricsToExtract``）；
* ``dataset``：是否会产生 DataVault 数据集。
"""

from __future__ import annotations

import json
import pathlib
from typing import Any, Dict, List, Optional

BACKENDS = ("lqcs", "qiskit", "offline_sim")

SERVER_ROOT = pathlib.Path(__file__).resolve().parents[3]
CONFIG_PATH = SERVER_ROOT / "config" / "experiment_configs.json"

# 上游真机词表（与 vendor/OpenQWF/adapters/lqcs.py 的 SQ_NODES 一致，2026-11-04 实测）
LQCS_NODES = (
    "iqraw", "piamp", "pidf", "pulse_shape", "pulse_shape_ramsey",
    "ramsey_df", "ramsey_t2", "s21", "s21_dis", "s21_power2d", "s21_zpa2d",
    "set_pi", "spectroscopy", "spectroscopy_auto", "spinecho_t2", "t1",
    "test_timing_xyz", "xeb",
)

# qiskit 词表用别名对齐（左 = vendor fn 尾名，右 = qiskit experiment）
QISKIT_ALIASES = {
    "ramsey_df": "ramsey",
    "ramsey_t2": "ramsey",
    "spectroscopy_iq": "pulsed_spec",
}

# 通用参数 schema（具体实验再叠加自己的）
_COMMON_PARAMS: Dict[str, Dict[str, Any]] = {
    "do_plot": {"type": "bool", "default": True, "note": "是否出图（服务内部默认不出图，避免拉 GUI）"},
    "shots": {"type": "int", "default": None, "note": "采样次数，模拟后端默认 2000"},
    "seed": {"type": "int", "default": None, "note": "随机种子，复现用"},
}

_KIND_PARAMS: Dict[str, Dict[str, Dict[str, Any]]] = {
    "spectroscopy": {"freq_range": {"type": "range", "default": None,
                                    "note": "[start, stop, step]，相对 f10 的偏移量（GHz）"}},
    "pulsed_spec": {"freq_range": {"type": "range", "default": None, "note": "同上"}},
    "t1": {"zpa": {"type": "float", "default": None, "note": "零偏置点；不给会跑 zpa×时间二维慢扫描"}},
    "ramsey": {"detuning_range": {"type": "range", "default": None, "note": "失谐扫描范围"}},
    "ramsey_df": {"detuning_range": {"type": "range", "default": None, "note": "失谐扫描范围"}},
    "piamp": {"amp_range": {"type": "range", "default": None, "note": "脉幅扫描范围"}},
    "time_rabi": {"amp_range": {"type": "range", "default": None, "note": "脉幅扫描范围"}},
    "rabi": {"amp_range": {"type": "range", "default": None, "note": "脉幅扫描范围"}},
    # vendor 的 sq.pidf 只认 df（标量半宽，GHz），给数组/换名字会扫空；
    # 上游 lint 对 |df| < 0.05 报 W_PIDF_DF_SMALL，所以默认给 0.05。
    "pidf": {"df": {"type": "float", "default": 0.05, "note": "失谐扫描半宽（GHz），太小会扫空"}},
    "xeb": {"depth": {"type": "int", "default": None, "note": "随机化深度（层数）"}},
    "drag_calibrate": {"drag_range": {"type": "range", "default": None, "note": "DRAG 系数扫描范围"}},
    "cz.dcz": {"coupler": {"type": "str", "default": None, "required": True,
                            "note": "耦合器名（CZ 要 2 比特 + 1 耦合器）"}},
}

_DESCRIPTIONS = {
    "s21": "腔频扫描，读 fread（读出腔共振）",
    "s21_dis": "色散频移，判断比特-腔耦合",
    "spectroscopy": "粗扫频，找比特共振 f10",
    "pulsed_spec": "脉冲谱（spectroscopy_iq），更准的比特频率",
    "iqraw": "IQ 采集，给区分度 contrast / SNR（态区分的第一步）",
    "single_shot": "单发读出，给读出保真度",
    "piamp": "π 脉冲幅度扫描（time_rabi 的 goto 版）",
    "time_rabi": "时间域 Rabi，找 π 脉冲时长",
    "rabi": "Rabi 振荡",
    "pidf": "功率依赖频移，contrast 卡住时先查它（改 f10 而不是硬调 piamp）",
    "ramsey": "Ramsey 干涉，给 T2* 与失谐",
    "ramsey_df": "Ramsey（detuning fit），给 T2* 与失谐",
    "ramsey_t2": "Ramsey T2",
    "t1": "弛豫时间 T1（不给 zpa 会跑二维慢扫描）",
    "xeb": "交叉熵基准，给单比特门保真度",
    "allxy": "AllXY，快速看脉冲质量",
    "drag_calibrate": "DRAG 系数标定，给 drag_delta",
    "pulse_shape": "脉冲形状诊断",
    "pulse_shape_ramsey": "脉冲形状 + Ramsey",
    "swap": "iSWAP 振荡（双比特）",
    "cz.dcz": "CZ 门调谐（双比特 + 耦合器，需要上游 DSL 支持）",
    "spectroscopy_auto": "自动扫频，给 zpa ↔ 比特频率曲线（变频比特）",
    "s21_zpa2d": "zpa × 频率二维腔谱",
    "s21_power2d": "功率 × 频率二维腔谱",
    "spinecho_t2": "自旋回波 T2",
    "set_pi": "直接写 π 脉幅参数（写操作）",
    "test_timing_xyz": "时序自检",
}

_CACHE: Optional[Dict[str, Dict[str, Any]]] = None


def _load_config() -> Dict[str, Any]:
    try:
        with CONFIG_PATH.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return {}
    return data.get("experiments") or {}


def _qiskit_kinds() -> set:
    """qiskit 后端当前支持的 experiment 名（探测失败就当空集）。"""
    try:
        from services.quantum_service.qiskit_experiments import supported_experiments
        return set(supported_experiments())
    except Exception:
        return set()


def _fn_kind(fn: str) -> str:
    """``sq.ramsey_df`` → ``ramsey_df``；``cz.dcz`` → ``cz.dcz``。"""
    fn = str(fn or "").strip()
    if fn.startswith("sq."):
        return fn[3:]
    return fn


def _build() -> Dict[str, Dict[str, Any]]:
    config = _load_config()
    qiskit = _qiskit_kinds()
    catalog: Dict[str, Dict[str, Any]] = {}

    for kind, entry in config.items():
        fn = str(entry.get("function") or f"sq.{kind}")
        tail = _fn_kind(fn)
        catalog[kind] = {
            "kind": kind,
            "fn": fn,
            "qubits": int(entry.get("qubits") or 1),
            "description": _DESCRIPTIONS.get(kind) or entry.get("description") or kind,
            "produces": list(entry.get("metricsToExtract") or []),
            "dataset": True,
            "params": {**_COMMON_PARAMS, **(_KIND_PARAMS.get(kind) or {})},
            "registered": True,
            "backends": _backends_for(fn, tail, qiskit),
        }

    for node in LQCS_NODES:
        kind = node
        if kind in catalog:
            continue
        fn = f"sq.{kind}"
        catalog[kind] = {
            "kind": kind,
            "fn": fn,
            "qubits": 1,
            "description": _DESCRIPTIONS.get(kind) or f"真机实验 {fn}（未登记在 experiment_configs.json）",
            "produces": [],
            "dataset": True,
            "params": {**_COMMON_PARAMS, **(_KIND_PARAMS.get(kind) or {})},
            "registered": False,
            "backends": _backends_for(fn, kind, qiskit),
        }

    return catalog


def _backends_for(fn: str, tail: str, qiskit: set) -> Dict[str, bool]:
    lqcs = fn.startswith("cz.") or tail in LQCS_NODES
    qk = QISKIT_ALIASES.get(tail, tail) in qiskit
    # offline_sim 按历史数据回放：任何 kind 都"支持"，但有没有数据取决于现场
    return {"lqcs": bool(lqcs), "qiskit": bool(qk), "offline_sim": True}


def _catalog() -> Dict[str, Dict[str, Any]]:
    global _CACHE
    if _CACHE is None:
        _CACHE = _build()
    return _CACHE


def reload() -> Dict[str, Dict[str, Any]]:
    """清缓存（测试与热改配置用）。"""
    global _CACHE
    _CACHE = None
    return _catalog()


def __getattr__(name: str):
    # CATALOG 做成惰性属性，避免 import 期就读盘（测试里可替换配置）
    if name == "CATALOG":
        return _catalog()
    raise AttributeError(name)


def get_spec(kind: str) -> Optional[Dict[str, Any]]:
    key = str(kind or "").strip()
    if not key:
        return None
    return _catalog().get(key)


def supported_kinds(backend: Optional[str] = None) -> List[str]:
    if backend:
        return sorted(k for k, v in _catalog().items() if v["backends"].get(backend))
    return sorted(_catalog())


def supports(kind: str, backend: str) -> bool:
    spec = get_spec(kind)
    if not spec:
        return False
    return bool(spec["backends"].get(backend))


def catalog_json(backend: Optional[str] = None) -> Dict[str, Any]:
    """``GET /experiments/catalog`` 的响应体。"""
    items = []
    for kind in supported_kinds(backend):
        spec = _catalog()[kind]
        items.append({
            "kind": spec["kind"],
            "fn": spec["fn"],
            "qubits": spec["qubits"],
            "description": spec["description"],
            "produces": spec["produces"],
            "dataset": spec["dataset"],
            "registered": spec["registered"],
            "backends": spec["backends"],
            "params": spec["params"],
            "supported_backends": [b for b in BACKENDS if spec["backends"].get(b)],
        })
    matrix = {b: sum(1 for v in _catalog().values() if v["backends"].get(b)) for b in BACKENDS}
    return {
        "ok": True,
        "kinds": len(items),
        "backends": list(BACKENDS),
        "backend_support": matrix,
        "experiments": items,
        "notes": [
            "kind 是语义名；fn 是 vendor 函数名，只在本服务内部使用",
            "offline_sim 对任何 kind 都算支持，但是否真有历史数据取决于现场",
            "参数 schema 里的 range 表示 [start, stop, step]",
        ],
    }
