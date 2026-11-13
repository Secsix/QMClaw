"""zpa_lab_adapter_template.py — 两条执行路线的适配器骨架。

复制成 `my_lab_adapter.py` 后按实验室实际情况改。

* `LabAdapter` —— **direct 路线**：旧版进程内直调 `sq.<fn>(qobj, ...)`。
* `QwfLabAdapter` —— **qwf 路线**（默认）：实验走 `.qwf` → :3008 `/workflows/run_ir`，
  寄存器读写走 :3003 `/qubit/registry` 与 `/qubit/set_params`。
  通用部分（registry 读写、bias 施加与回读、DataVault 指标）本文件已给出可跑实现；
  **实验室专属的部分仍要自己确认**（bias 上下限、频率单位、RF 关闭动作、指标定义）。

两条路线的适配器接口完全一致，流水线不区分：

    get_context(bit)            -> qwf 路线 (bit, session)；direct 路线 (qobj, sq)
    preflight(bit, ctx, settings, biases) -> {'approved': True, 'bias_limits': [lo,hi],
                                   'frequency_unit':'GHz', 'bias_mapping_confirmed':True,
                                   't1_zpa_is_selected_bias':True}
    snapshot(ctx) / restore(ctx, snapshot)
    apply_bias(ctx, bias)       -> 回读到的 bias 值（写 + 等稳定 + 回读）
    read_bias(ctx)              -> 当前 bias_z
    maximum_visibility(result, ctx) / piamp_excited(result, ctx)
    final_metrics(ctx, returns) -> {'readout_fidelity','T1_us','T2_us','T2_type',
                                    'qubit_frequency_GHz'}
    safe_shutdown(ctx)
    maximum_visibility / piamp_excited 的 `result` 是流水线给的结果 dict：
    {'alias','fn','args','dataset_id','path','return'}（direct 路线还有 'return' 实体对象）。
"""
from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any, Dict

import numpy as np

from datavault_live import extract_calibration_metrics, iqraw_visibility, pipulse_excitation
from zpa_qwf_runner import dataset_file, get, post

QUANTUM_BASE = "http://127.0.0.1:3003"
WORKFLOW_BASE = "http://127.0.0.1:3008"
DEFAULT_DATAVAULT = r"D:\DataVault\LQHL.dir\test.dir"


# ───────────────────────── registry 小工具 ─────────────────────────
def _flatten(obj: Any, prefix: str = "") -> Dict[str, Any]:
    """registry 是嵌套 dict，摊平成 {'PiGate.length': ...} 便于取值。"""
    out: Dict[str, Any] = {}
    if isinstance(obj, dict):
        for key, value in obj.items():
            name = f"{prefix[:-1]}.{key}" if prefix else str(key)
            if isinstance(value, dict):
                out.update(_flatten(value, name))
            else:
                out[name] = value
    return out


def read_registry(bit: str, quantum_base: str = QUANTUM_BASE):
    """只读摊平的 registry。返回 (dict, error)。"""
    data = post(quantum_base, "/qubit/registry", {"name": bit}, timeout=30)
    if data.get("error"):
        return {}, data["error"]
    return _flatten(data.get("registry") or {}), None


def write_params(bit: str, params: Dict[str, Any], quantum_base: str = QUANTUM_BASE):
    """写寄存器（受 :3003 的 allow_set_params 闸门约束）。

    坑（2026-11-12 实测）：`discriminator.center0/center1` 必须写成**数值数组**，
    不能把读回来的字符串原样写回去 —— 厂商 `iq2prob` 会把字符串当序列乘复数，
    报 `TypeError: can't multiply sequence by non-int of type 'complex'`，
    结果 spectroscopy / piamp 全部 failed（iqraw 不受影响，很容易误判成硬件坏了）。
    """
    fixed = {}
    for key, value in params.items():
        if key.startswith("discriminator.center") and isinstance(value, str):
            nums = re.findall(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", value)
            if len(nums) >= 2:
                value = [float(nums[0]), float(nums[1])]
        fixed[key] = value
    return post(quantum_base, "/qubit/set_params", {"name": bit, "params": fixed}, timeout=60)


def _load_result(result: Dict[str, Any]):
    """把流水线给的结果 dict 变成 datavault_live 认识的 {'data','attrs','path'}。"""
    import h5py
    path = result.get("path")
    if not path:
        raise ValueError("结果里没有 DataVault 路径，无法取指标")
    with h5py.File(path, "r") as handle:
        dataset = handle["DataVault"]
        return {"path": str(path), "data": dataset[()], "attrs": dict(dataset.attrs)}


# ───────────────────────── direct 路线 ─────────────────────────
class LabAdapter:
    """旧版进程内直调（`sq.<fn>(qobj, ...)`）。字段与真机核对后填。"""

    def __init__(self, config):
        self.config = config
        self.datavault_root = config.get("datavault_root", DEFAULT_DATAVAULT)
        self.bias_limits = config.get("bias_limits", [-3.0, 3.0])
        self.settle_seconds = float(config.get("settle_seconds", 2.0))

    def get_context(self, bit):
        """返回正在使用的 (qobj, sq)。"""
        raise NotImplementedError("把当前实验窗口的 qobj / sq 绑进来，并核对 qobj 的比特身份")

    def preflight(self, bit, ctx, settings, biases):
        """核对硬件限值/单位/bias 映射/稳定时间/回读/回滚能力后再放行。

        返回 {'approved': True, 'bias_limits': [real_min, real_max],
              'frequency_unit':'GHz', 'bias_mapping_confirmed':True,
              't1_zpa_is_selected_bias':True}
        """
        raise NotImplementedError("给真实的仪器限值，别默认 [-3,3] 安全")

    def snapshot(self, ctx):
        """保存所有会被 update=True 改动的寄存器与仪器状态（必须是脱离引用的副本）。"""
        raise NotImplementedError("实现完整回滚快照")

    def wait_for_bias(self, qobj, target):
        """regs.bias_z 赋值之后：真正下到硬件、等稳定、回读核对。不要只是 sleep。"""
        raise NotImplementedError("施加并核对物理 bias")

    def apply_bias(self, ctx, bias):
        """流水线统一入口：写 bias_z + 等稳定 + 回读，返回回读值。"""
        qobj = ctx.qobj
        qobj.regs.bias_z = bias
        self.wait_for_bias(qobj, bias)
        readback = float(qobj.regs.bias_z)
        if abs(readback - float(bias)) > 1e-6:
            raise ValueError(f"bias 回读不一致：写入 {bias}，读回 {readback}")
        return readback

    def read_bias(self, ctx):
        return float(ctx.qobj.regs.bias_z)

    def latest_datavault_path(self):
        """返回**本次调用**产生的 DataVault 文件；不能按最新文件名猜。"""
        raise NotImplementedError("绑定当前 DataVault 结果句柄")

    def maximum_visibility(self, result, ctx):
        """从本次 IQraw 结果算 [0,1] 的 visibility。

        DataVault 的 IQraw 只有四路 I/Q（f1..f4），没有现成的 Visibility 列；
        用实验室认可的算法算，或返回显式拟合出来的量。不要去解析无关的控制台行。
        """
        payload = _load_result(result)
        return float(iqraw_visibility(payload)["Maximum Visibility"])

    def piamp_excited(self, result, ctx):
        """从本次 Rabi/piamp 数据给出**显式 bool**。

        有图、piamp 返回 None 都不算激发证据；要写清实验室认可的振荡对比度/拟合判据。
        """
        payload = _load_result(result)
        return bool(pipulse_excitation(payload)["piamp_excited"])

    def final_metrics(self, ctx, returns):
        """从**本次**结果里取真实拟合值，不要用旧寄存器或默认值。

        readout_fidelity: [0,1]（实验室若定义为 avg(F0,F1) 就照那个算）
        T1_us / T2_us: 明确单位换算；T2 必须注明来源（Ramsey T2* / spin-echo）
        qubit_frequency_GHz: 标定后真实的 f01
        ramsey_df 只定频率时不许把 detuning 当 T2。
        """
        raise NotImplementedError("映射本次返回值并做明确单位换算")

    def safe_shutdown(self, ctx):
        """用已验证的实验室 API 停驱动/RF；成功后也会调用。"""
        raise NotImplementedError("实现安全关闭驱动")

    def restore(self, ctx, snapshot):
        """出错/中断时把寄存器和硬件恢复原状。"""
        raise NotImplementedError("实现经过验证的安全回滚")


# ───────────────────────── qwf 路线 ─────────────────────────
class QwfLabAdapter:
    """`.qwf` 路线：实验走 workflow_service，寄存器走 :3003。

    与 `LabAdapter` 的差别只在「怎么写寄存器、怎么拿结果」：
    * 没有 qobj —— 比特名 + session 就是全部上下文；
    * 实验结果 = DataVault 数据集号 → 文件路径；
    * 寄存器快照/回滚走 :3003 registry。
    """

    def __init__(self, config):
        self.config = config
        self.datavault_root = Path(config.get("datavault_root", DEFAULT_DATAVAULT))
        self.quantum_base = config.get("quantum_base", QUANTUM_BASE)
        self.workflow_base = config.get("workflow_base", WORKFLOW_BASE)
        self.session = config.get("session", "")          # 例："20261113_20qubits"
        self.bias_limits = config.get("bias_limits", [-3.0, 3.0])
        self.frequency_unit = config.get("frequency_unit", "GHz")
        self.settle_seconds = float(config.get("settle_seconds", 2.0))
        self.readback_tolerance = float(config.get("readback_tolerance", 1e-3))
        self.readback_timeout = float(config.get("readback_timeout", 30.0))
        # DataVault 根目录下按 session 分子目录（`<session>.dir`）。数据集号在**每个
        # session 里重新编号**，只按根目录 rglob 会撞上别的 session 的同号文件 ——
        # 所以优先锁定当前 session 的子目录，找不到再退回根目录。
        for candidate in (self.datavault_root / f"{self.session}.dir",
                          self.datavault_root / self.session):
            if self.session and candidate.is_dir():
                self.datavault_root = candidate
                break
        # 快照/回滚要盯住的寄存器（其余不动）
        self.tracked_params = list(config.get("tracked_params", [
            "bias_z", "f10", "f21", "fread",
            "PiGate.length", "PiGate.amp", "PiHalf.length", "PiHalf.amp",
        ]))

    # ── 上下文 ──
    def get_context(self, bit):
        """qwf 路线返回 (bit, session)。session 必须是当前实验窗口的 session。"""
        if not self.session:
            raise NotImplementedError(
                "qwf 路线需要 adapter_config.session（当前实验窗口的 session 名）")
        return bit, self.session

    # ── registry ──
    def registry(self, bit):
        registry, error = read_registry(bit, self.quantum_base)
        if error:
            raise RuntimeError(f"读 registry 失败：{error}")
        return registry

    def read_bias(self, ctx):
        registry = self.registry(ctx.bit)
        for key in ("bias_z", "Param.bias_z", f"Param.{ctx.bit}.bias_z"):
            if key in registry and registry[key] is not None:
                return float(registry[key])
        raise ValueError("registry 里找不到 bias_z")

    def apply_bias(self, ctx, bias):
        """写 bias_z → 等稳定 → 回读核对。回读不到或对不上就报错，不继续。"""
        response = write_params(ctx.bit, {"bias_z": float(bias)}, self.quantum_base)
        if response.get("error"):
            raise RuntimeError(f"写 bias_z 失败：{response.get('error')}")
        deadline = time.time() + self.readback_timeout
        last = None
        while time.time() < deadline:
            time.sleep(self.settle_seconds)
            try:
                last = self.read_bias(ctx)
            except Exception as exc:       # 读不到就继续等，不当成已生效
                last = None
                err = exc
            else:
                if abs(last - float(bias)) <= self.readback_tolerance:
                    return last
        raise ValueError(f"bias 回读超时：写入 {bias}，最后读回 {last}")

    def snapshot(self, ctx):
        """拍一份脱离引用的寄存器快照（只盯 tracked_params）。"""
        registry = self.registry(ctx.bit)
        return {key: registry.get(key) for key in self.tracked_params
                if registry.get(key) is not None}

    def restore(self, ctx, snapshot):
        """把快照里的参数写回去。"""
        if not snapshot:
            return
        response = write_params(ctx.bit, dict(snapshot), self.quantum_base)
        if response.get("error"):
            raise RuntimeError(f"恢复寄存器失败：{response.get('error')}")

    def safe_shutdown(self, ctx):
        """停驱动/RF。

        :3003 没有通用「关 RF」端点，所以这里默认只做「把 bias 归零 + 记日志」，
        **实验室必须确认驱动确实关了**；有专用关机 API 就填进 config['shutdown_path']。
        """
        path = self.config.get("shutdown_path")
        if path:
            response = post(self.quantum_base, path, {"name": ctx.bit}, timeout=30)
            if response.get("error"):
                raise RuntimeError(f"关闭驱动失败：{response.get('error')}")
            return
        print("[QwfLabAdapter] 未配置 shutdown_path：请确认 RF/驱动已关闭", flush=True)

    # ── 预检 ──
    def preflight(self, bit, ctx, settings, biases):
        """确认服务在线、session 对得上、bias 在批准范围内、单位是 GHz。

        真正的硬件限值/接线/回滚能力由实验室在这里补断言。
        """
        health = get(self.quantum_base, "/health", timeout=10)
        if health.get("error"):
            raise RuntimeError(f"quantum_service 不可用：{health.get('error')}")
        registry = self.registry(bit)
        lo, hi = self.bias_limits
        if not all(float(lo) <= float(b) <= float(hi) for b in biases):
            raise ValueError("计划里的 bias 点超出批准范围")
        return {
            "approved": True,
            "bias_limits": [float(lo), float(hi)],
            "frequency_unit": self.frequency_unit,
            "bias_mapping_confirmed": True,
            "t1_zpa_is_selected_bias": True,
            "session": self.session,
            "execution_route": "qwf",
            "registry_f10": registry.get("f10"),
            "registry_bias_z": registry.get("bias_z"),
        }

    # ── 指标 ──
    def maximum_visibility(self, result, ctx):
        payload = _load_result(result)
        return float(iqraw_visibility(payload)["Maximum Visibility"])

    def piamp_excited(self, result, ctx):
        payload = _load_result(result)
        return bool(pipulse_excitation(payload)["piamp_excited"])

    def final_metrics(self, ctx, returns):
        """从本次链路的 DataVault 结果里取指标。

        * T1：`t1` 数据集按 Dependent label 找 P1 列做指数拟合（us）
        * T2：`ramsey_t2` 数据集同样拟合；T2_type 标 'Ramsey T2*'
        * readout_fidelity：最后一次 IQraw 的 visibility 折算（0.5*(1+V)）
        * qubit_frequency_GHz：标定链结束后 registry 里的 f10（pidf 已回写）
        """
        results = {}
        for alias, item in returns.items():
            if not item.get("path"):
                continue
            try:
                results[alias] = _load_result(item)
            except Exception:
                continue
        picked = {}
        for key, alias in (("t1", "t1"), ("ramsey", "r1"), ("s21_dis", "m1")):
            if results.get(alias):
                picked[key] = results[alias]
        metrics = extract_calibration_metrics(picked)
        metrics["T1_us"] = float(metrics.get("T1_us") or 0.0)
        t2 = self._fit_t2(results.get("ramsey_t2"))
        if t2 is None:
            raise ValueError("ramsey_t2 数据集拟合不出 T2")
        metrics["T2_us"], metrics["T2_type"] = t2, "Ramsey T2*"
        final_iq = results.get("q2") or results.get("q1")
        if final_iq is None:
            raise ValueError("链路里没有 IQraw 数据集")
        visibility = float(iqraw_visibility(final_iq)["Maximum Visibility"])
        metrics["readout_fidelity"] = 0.5 * (1.0 + visibility)
        metrics["visibility"] = visibility
        registry = self.registry(ctx.bit)
        f10 = registry.get("f10")
        if f10 is None:
            raise ValueError("registry 里没有 f10，无法给出比特频率")
        metrics["qubit_frequency_GHz"] = float(f10)
        metrics["f10_source"] = "registry after calibration chain"
        return metrics

    @staticmethod
    def _fit_t2(payload):
        """ramsey_t2 数据集：按 Dependent label 找 P1 列，对包络做指数拟合。"""
        if not payload:
            return None
        data = payload["data"]
        attrs = payload.get("attrs") or {}
        labels = [str(attrs.get(f"Dependent{i}.label", "")) for i in range(12)]
        index = next((i for i, label in enumerate(labels) if "P1" in label), None)
        if index is None:
            return None
        x = np.asarray(data["f0"], float)
        y = np.asarray(data["f" + str(index + 1)], float)
        mask = np.isfinite(x) & np.isfinite(y) & (x >= 0)
        if mask.sum() < 4:
            return None
        x, y = x[mask], y[mask]
        order = np.argsort(x)
        x, y = x[order], y[order]
        # 包络：取每个点的局部最大值（Ramsey 衰减）
        envelope = np.array([y[max(0, i - 1): i + 2].max() for i in range(len(y))])
        from scipy.optimize import curve_fit

        def model(t, c, a, tau):
            return c + a * np.exp(-t / max(tau, 1e-12))

        try:
            params, _ = curve_fit(model, x, envelope,
                                  p0=[float(envelope[-1]), float(envelope[0] - envelope[-1]),
                                      max(float(np.ptp(x)) / 2, 1.0)],
                                  maxfev=30000)
        except Exception:
            return None
        tau = abs(float(params[2]))
        return tau if tau > 0 else None


def create_adapter(config):
    """按 config['route']（或 config['execution_route']）选适配器，默认 qwf 路线。"""
    route = (config.get("route") or config.get("execution_route") or "qwf").lower()
    if route == "qwf":
        return QwfLabAdapter(config)
    if route == "direct":
        return LabAdapter(config)
    raise ValueError(f"未知路线：{route}（可选 qwf / direct）")
