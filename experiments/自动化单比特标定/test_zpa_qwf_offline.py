"""test_zpa_qwf_offline.py — 旧版（legacy API）标定流水线的**离线**自检。

不连服务、不碰硬件：只验证
  1. 参数名是旧版 legacy 口径（拒绝新版 piAmp / target_gate）；
  2. 生成的 `.qwf` 能被仓库里的 DSL 解析、校验、lint 通过（能找到 DSL 时）；
  3. 标定链的节点顺序与旧版序列一致；
  4. 离线规划能从示例 S21ZPA 文件里算出 bias 候选。

用法：
    python test_zpa_qwf_offline.py
退出码：0 = 全过；1 = 有失败。
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import zpa_qwf_runner as runner                      # noqa: E402
import zpa_calibration_pipeline as pipeline          # noqa: E402

FAILURES = []


def check(name, condition, detail=""):
    if condition:
        print(f"  [PASS] {name}")
    else:
        print(f"  [FAIL] {name} {detail}")
        FAILURES.append(name)


def dsl_available():
    """能不能用仓库里的 DSL 做离线 parse/validate/lint。"""
    server = HERE.parent / "server"
    if not server.is_dir():
        return None
    sys.path.insert(0, str(server))
    try:
        from services.workflow_service.dsl import lint_text, parse_qwf, validate
    except Exception:
        return None
    return lint_text, parse_qwf, validate


def legacy_chain(settings):
    """和 run_bit 里同款的标定链（保持两处一致）。"""
    b = 0.0
    return [
        runner.step("d1", "sq.pidf", {"df": settings.pidf_df}, 600, "pidf"),
        runner.step("d2", "sq.pidf", {}, 600, "pidf"),
        runner.step("r1", "sq.ramsey_df", {}, 900, "ramsey_df"),
        runner.step("m1", "sq.s21_dis", {}, 400, "s21_dis"),
        runner.step("q1", "sq.iqraw", {}, 300, "iqraw"),
        runner.step("x1", "sq.set_pi", {"gate": "X"}, 300, "set_pi X"),
        runner.step("x2", "sq.set_pi", {"gate": "X/2"}, 300, "set_pi X/2"),
        runner.step("t1", "sq.t1", {"zpa": b}, 1800, "t1"),
        runner.step("e1", "sq.ramsey_t2", {}, 1800, "ramsey_t2"),
        runner.step("xb", "sq.xeb", {}, 3600, "xeb"),
        runner.step("q2", "sq.iqraw", {}, 300, "iqraw"),
    ]


def main():
    print("== 1. legacy 参数名纪律 ==")
    try:
        runner.step("p1", "sq.piamp", {"piAmp": 2}, 300, "")
        check("拒绝新版参数名 piAmp", False, "居然通过了")
    except ValueError as exc:
        check("拒绝新版参数名 piAmp", True)
    try:
        runner.step("x1", "sq.set_pi", {"target_gate": "X"}, 300, "")
        check("拒绝新版参数名 target_gate", False, "居然通过了")
    except ValueError:
        check("拒绝新版参数名 target_gate", True)
    try:
        runner.step("s1", "sq.spectroscopy", {"freq": {"$arange": [5.5, 4.0, -0.001]}}, 300, "")
        check("接受旧版参数 freq", True)
    except ValueError as exc:
        check("接受旧版参数 freq", False, str(exc))
    try:
        runner.step("s1", "sq.spectroscopy", {}, 300, "")
        check("sq.spectroscopy 必须给 freq", False, "居然通过了")
    except ValueError:
        check("sq.spectroscopy 必须给 freq", True)
    try:
        runner.step("t1", "sq.t1", {}, 300, "")
        check("sq.t1 必须给 zpa", False, "居然通过了")
    except ValueError:
        check("sq.t1 必须给 zpa", True)

    print("== 2. 生成的 .qwf 用的是旧版调用形式 ==")
    settings = pipeline.Settings()
    chain = legacy_chain(settings)
    text = runner.build_qwf("test_chain", "旧版标定链自检", "q23_7", "20260822_device1",
                            runner.default_budget(chain, "180m"), chain)
    for expect in ("sq.pidf", "sq.ramsey_df", "sq.s21_dis", "sq.iqraw",
                   "sq.set_pi", "sq.t1", "sq.ramsey_t2", "sq.xeb",
                   'gate "X"', 'gate "X/2"', "df 0.08"):
        check(f".qwf 含 {expect}", expect in text)
    for forbid in ("piAmp", "target_gate", "sq.Ramsey_T2", "sq.CPMG_T2", "sq.XEB"):
        check(f".qwf 不含 {forbid!r}", forbid not in text)
    # session 只允许出现在 env 块里（实验参数里绝不传 session —— 那是新版 v1.0 的口径）
    check("session 只出现在 env 块", text.count("session") == 1, f"出现 {text.count('session')} 次")
    check("实验参数里没有 session", "session " not in text.split("args {", 1)[-1])

    print("== 3. 标定链顺序 ==")
    order = [s["fn"] for s in chain]
    expected = ["sq.pidf", "sq.pidf", "sq.ramsey_df", "sq.s21_dis", "sq.iqraw",
                "sq.set_pi", "sq.set_pi", "sq.t1", "sq.ramsey_t2", "sq.xeb", "sq.iqraw"]
    check("链顺序与旧版序列一致", order == expected, f"实际 {order}")

    print("== 4. DSL 离线 parse / validate / lint ==")
    tools = dsl_available()
    if tools is None:
        print("  [SKIP] 找不到仓库里的 DSL（server）")
    else:
        lint_text, parse_qwf, validate = tools
        try:
            ir = parse_qwf(text)
            check("parse_qwf 通过", isinstance(ir, dict) and bool(ir.get("pipeline")))
            normalized, diags = validate(ir, require_writes=False)
            errors = [d for d in diags if d.get("level") == "error"]
            check("validate 无 error", not errors,
                  str([f"{d.get('code')} {d.get('message')}" for d in errors[:3]]))
            report = lint_text(text)
            counts = report.get("counts") or {}
            check("lint 无 error", counts.get("error", 0) == 0,
                  str([e.get("message") for e in (report.get("errors") or [])[:3]]))
            if counts.get("warning"):
                print(f"  [INFO] lint warning: "
                      f"{[w.get('message') for w in (report.get('warnings') or [])[:5]]}")
        except Exception as exc:
            check("DSL 离线检查", False, repr(exc))

    print("== 5. 离线规划（示例 S21ZPA 文件）==")
    sample = HERE / "00731 - q23_7%c S21zpa2d.hdf5"
    if not sample.exists():
        print("  [SKIP] 没有示例 S21ZPA 文件")
    else:
        try:
            inspection = pipeline.inspect_file(sample, settings)
            check("示例文件可解析", inspection.get("bit") == "q23_7",
                  f"bit={inspection.get('bit')}")
            check("能算出 bias 候选", bool(inspection.get("scan_bias_values")),
                  str(inspection.get("scan_bias_values")))
        except Exception as exc:
            check("示例文件可解析", False, repr(exc))

    print()
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} 项 —— {FAILURES}")
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
