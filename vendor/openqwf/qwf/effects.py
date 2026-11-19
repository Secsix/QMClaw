"""写副作用语义表（核心层）。

R2 分层：这张表描述"哪些实验会自己改哪些寄存器"，属于**执行语义**而不是厂商适配细节
——lint、manifest、executor 三处都要用它做静态判定（写闸门预览、undeclared 对账、
参数自检）。原先它住在 `adapters/lqcs.py`（适配器）里，核心层只能反向 import 适配器，
方向是反的；R2 把它搬到核心，适配器改成 `from ..effects import WRITE_EFFECTS, write_effects`。

三档语义（差别不在文档里、在 lqms 源码里，所以写死成表）：

- `always`      调用处没把 `update` 传下去（或被调方根本没有 update 参数）
                → 无论你传 update=False 都会落盘，只能靠"不跑它"来避免
- `default_on`  签名 `update=True` 且真的传下去了 → 默认写，传 update=False 可关
- `none`        函数体里没有 update_* 调用 → 不写

`evidence` 是复核依据（`sq_nodes.py` 行号）；`tools/lqms_write_audit.py`
可以从源码重新生成这份事实表，selftest 的 G3 组负责盯住"表与源码不再一致"。
"""

from __future__ import annotations

from typing import Any, Dict, Optional

WRITE_EFFECTS: Dict[str, Dict[str, Any]] = {
    # ── always：关不掉 ────────────────────────────────────────────────────
    "sq.s21_dis": {
        "mode": "always", "writes": ["fread"],
        "evidence": "sq_nodes.py:399 签名无 update；:422 update_fread(...) 未传 update"
                    "（传 update=False 会被 **refresh_kwargs 吞掉，照样写）",
    },
    "sq.set_pi": {
        "mode": "always", "writes": ["PiGate.amp", "PiGate.alpha"],
        "evidence": "sq_nodes.py:603 签名无 update；:625 update_amp / :637 update_alpha 无条件；"
                    "base_experiment.py:2490-2497 update_amp 写 [(gate,'amp')]，"
                    ":2561-2567 update_alpha 写 [(gate,'alpha')]，"
                    "gate 由 :2383/:2520 {'X':'PiGate','X/2':'PiHalf'} 决定"
                    "（真机 2026-11-04 实测：gate=X 写 PiGate.*，gate=X/2 写 PiHalf.*）",
    },
    "sq.iqcenter": {
        "mode": "always",
        "writes": ["fread", "ReadIn.power", "ReadIn.zpa", "ReadIn.length",
                   "timing.adc_start", "ReadIn.ring_power", "fc_read"],
        "evidence": "sq_nodes.py:836 调 update_optimal_params；base_experiment.py:1773 该函数"
                    "无 update 参数；:1795-1848 按 best_params 写 fread / ReadIn.power / "
                    "ReadIn.zpa / ReadIn.length / timing.adc_start / ReadIn.ring_power / fc_read",
    },
    # ── default_on：默认写，显式 update=False 可关 ─────────────────────────
    "sq.piamp": {"mode": "default_on", "flag": "update",
                 "writes": ["PiGate.amp", "PiHalf.amp"],
                 "evidence": "sq_nodes.py:57 update_amp(..., update=update)，签名默认 True；"
                             "base_experiment.py:581-587 update_para(keys=[('PiGate','amp'),"
                             "('PiHalf','amp')])（真机 2026-11-04 实测两个都写）"},
    "sq.pidf": {"mode": "default_on", "flag": "update", "writes": ["f10", "f21"],
                "evidence": "sq_nodes.py:92 update_f10(..., update=update)，签名默认 True；"
                            "base_experiment.py:611-617 update_para(keys=['f10','f21'])"
                            "（改 f10 会带着 f21 一起改）"},
    "sq.pidf21": {"mode": "default_on", "flag": "update", "writes": ["f21"],
                  "evidence": "sq_nodes.py:132 update_f21(..., update=update)，签名默认 True"},
    "sq.ramsey_df": {"mode": "default_on", "flag": "update", "writes": ["f10", "f21"],
                     "evidence": "sq_nodes.py:167 update_f10(..., update=update)，签名默认 True；"
                                 "base_experiment.py:951-962 keys=['f10','f21']（同样带着 f21）"},
    "sq.s21": {"mode": "default_on", "flag": "update", "writes": ["fread"],
               "evidence": "sq_nodes.py:209 update_fread(data, update=update)，签名默认 True"},
    "sq.s21_power2d": {"mode": "default_on", "flag": "update", "writes": ["ReadIn.power"],
                       "evidence": "sq_nodes.py:278 update_power(..., update=update)，签名默认 True；"
                                   "base_experiment.py:721-731 update_para(keys=[('ReadIn','power')])"},
    "sq.spectroscopy": {"mode": "default_on", "flag": "update", "writes": ["f10", "f21"],
                        "evidence": "sq_nodes.py:312 update_f10(..., update=update)，签名默认 True；"
                                    "base_experiment.py:868-872 update_para(keys=['f10','f21'])"},
    "sq.iqraw": {"mode": "default_on", "flag": "update",
                 "writes": ["discriminator.center0", "discriminator.center1",
                            "discriminator.radius0", "discriminator.method"],
                 "evidence": "sq_nodes.py:382 update_iq_zero(update=update)；:386 if update: "
                             "update_iq(auto=auto)；update_iq(base_experiment.py:1114) 无 update 参数；"
                             ":1137-1146 update_iq_zero 写 center0/radius0/method，"
                             ":1125-1127 update_iq 写 center0/center1"},
    "sq.test_timing_xyz": {"mode": "default_on", "flag": "update",
                           "writes": ["timing.xy", "timing.z"],
                           "evidence": "sq_nodes.py:527-530 update=='xy'|True→update_xy，"
                                       "=='z'→update_z；默认 update='xy'"},
    # ── none：不写 ────────────────────────────────────────────────────────
    "sq.t1": {"mode": "none", "writes": [],
              "evidence": "sq_nodes.py:451 有 update 参数但函数体无 update_* 调用"},
    "sq.ramsey_t2": {"mode": "none", "writes": [], "evidence": "sq_nodes.py:540 同理"},
    "sq.spinecho_t2": {"mode": "none", "writes": [], "evidence": "sq_nodes.py:573 同理"},
    "sq.time_rabi": {"mode": "none", "writes": [], "evidence": "sq_nodes.py:327 无 update 语义"},
    "sq.s21_zpa2d": {"mode": "none", "writes": [],
                     "evidence": "sq_nodes.py:239 update_zeff(...) 被注释掉"},
    "sq.spectroscopy_auto": {"mode": "none", "writes": [], "evidence": "sq_nodes.py:426 无 update_*"},
    "sq.spectroscopy_iq": {"mode": "none", "writes": [], "evidence": "sq_nodes.py 无 update_*"},
    "sq.xeb": {"mode": "none", "writes": [], "evidence": "sq_nodes.py:672 无 update_*"},
    "sq.pulse_shape": {"mode": "none", "writes": [], "evidence": "sq_nodes.py:699 无 update_*"},
    "sq.pulse_shape_ramsey": {"mode": "none", "writes": [],
                              "evidence": "sq_nodes.py:743 无 update_*"},
    "sq.impa_gain": {"mode": "none", "writes": [], "evidence": "sq_nodes.py:756 空函数"},
}


def write_effects(fn: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """这条实验下发后会不会自己改参数、改哪些。

    `effective=True` 表示**这次真的会写**（已把 `update=False` 这种显式关闭算进去）。
    未知函数返回 `mode="unknown"`、`effective=False`：不拦，但要报出来。
    """
    params = params or {}
    name = str(fn or "")
    if "." not in name:
        name = f"sq.{name}"
    spec = WRITE_EFFECTS.get(name)
    if spec is None:
        return {"fn": name, "mode": "unknown", "writes": [], "effective": False,
                "reason": f"未知实验 {name} 的写入语义（不拦，建议补进 WRITE_EFFECTS）"}
    mode = spec["mode"]
    writes = list(spec.get("writes") or [])
    flag = spec.get("flag")
    reason = spec.get("evidence", "")
    if name == "sq.set_pi":
        # 写哪个门由 gate 决定：X → PiGate，X/2 → PiHalf（base_experiment.py:2383/:2520）
        gate_name = {"X": "PiGate", "X/2": "PiHalf"}.get(str(params.get("gate") or "X"), "PiGate")
        writes = [f"{gate_name}.amp", f"{gate_name}.alpha"]
    if mode == "none" or not writes:
        return {"fn": name, "mode": mode, "writes": [], "effective": False, "reason": reason}
    if mode == "default_on" and flag:
        val = params.get(flag, True)
        if val is False:
            return {"fn": name, "mode": mode, "writes": [], "effective": False,
                    "reason": f"调用方显式 {flag}=False，本次不写"}
    if mode == "always" and params.get(flag or "update") is False:
        reason = f"该函数忽略 {flag or 'update'}=False，仍会写。" + reason
    return {"fn": name, "mode": mode, "writes": writes, "effective": True, "reason": reason}
