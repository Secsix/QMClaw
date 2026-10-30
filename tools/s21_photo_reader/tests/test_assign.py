"""LabRAD fread 赋值逻辑的回归测试（用 mock 比特对象代替真设备）。

用法: python tests/test_assign.py
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from s21pr import assign            # noqa: E402


class FakeRegs:
    """模拟 LabRAD 的寄存器容器：setattr 立即生效，可读回。"""

    def __init__(self, **kw):
        self.__dict__.update(kw)


class FakeQubit:
    def __init__(self, **regs):
        self.regs = FakeRegs(**regs)


def make_namespace():
    return {
        "q21_5": FakeQubit(fread=6.40),
        "q19_7": FakeQubit(fread=6.45),
        "q17_9": FakeQubit(fread=6.39),
        "q15_11": FakeQubit(fread=6.28),
        "q13_13": FakeQubit(fread=6.35),
        "q11_15": FakeQubit(fread=6.44),
        "q9_17": FakeQubit(fread=6.50),
        "q7_19": FakeQubit(fread=6.62),
        "q5_21": FakeQubit(fread=6.54),
    }


def main():
    tmp = ROOT / "_tmp_test"
    tmp.mkdir(exist_ok=True)
    csv_path = tmp / "assign_test.csv"
    csv_path.write_text(
        "qubit,s21_dip_value,unit,dip_x_px\n"
        "q21_5,6.556,GHz,811.0\n"
        "q19_7,6.469,GHz,645.1\n"
        "q17_9,6.393,GHz,505.9\n"
        "q15_11,6.2835,GHz,304.0\n"
        "q13_13,6.353,GHz,432.2\n"
        "q11_15,6.445,GHz,604.5\n"
        "q9_17,6.504,GHz,707.8\n"
        "q7_19,6.6215,GHz,924.2\n"
        "q5_21,6.5465,GHz,787.3\n",
        encoding="utf-8")

    ok = True

    # 1) 解析 CSV
    rows = assign.parse_assignments(csv_path)
    print(f"[1] 解析到 {len(rows)} 条: " +
          ", ".join(f"{r.qubit}={r.value}" for r in rows))
    if len(rows) != 9:
        print("[FAIL] 应为 9 条"); ok = False
    if abs(rows[0].value - 6.556) > 1e-9:
        print("[FAIL] 首条数值不对"); ok = False

    # 2) dry-run 不写硬件
    ns = make_namespace()
    res = assign.apply_fread(rows, namespace=ns, dry_run=True)
    if ns["q21_5"].regs.fread != 6.40:
        print("[FAIL] dry-run 却改了寄存器"); ok = False
    print(f"[2] dry-run: {sum(1 for r in res if r.ok)}/{len(res)} 预览，寄存器未变 ✓")

    # 3) 真实写入 + 回读
    res = assign.apply_fread(rows, namespace=ns)
    good = sum(1 for r in res if r.ok)
    print(f"[3] 写入: {good}/{len(res)} 成功")
    if good != 9:
        print("[FAIL] 写入未全部成功")
        print(assign.format_report(res))
        ok = False
    if abs(ns["q21_5"].regs.fread - 6.556) > 1e-12:
        print("[FAIL] q21_5.fread 未更新"); ok = False
    if abs(ns["q5_21"].regs.fread - 6.5465) > 1e-12:
        print("[FAIL] q5_21.fread 未更新"); ok = False

    # 4) 不存在的比特要报错但不影响其它
    rows2 = assign.Assignment(qubit="q99_99", value=6.0), *rows
    res2 = assign.apply_fread(rows2, namespace=ns)
    bad = [r for r in res2 if not r.ok]
    print(f"[4] 缺失比特处理: {len(bad)} 条失败（应为 1），其余 {len(res2)-len(bad)} 条成功")
    if len(bad) != 1 or "q99_99" not in bad[0].error:
        print("[FAIL] 缺失比特处理异常"); ok = False

    # 5) 文本格式（用户粘贴的赋值行）
    txt = tmp / "paste.txt"
    txt.write_text("q21_5.regs.fread = 6.556\nq19_7 6.469\nq17_9,6.393\n",
                   encoding="utf-8")
    rows3 = assign.parse_assignments(txt)
    print(f"[5] 文本解析: " + ", ".join(f"{r.qubit}={r.value}" for r in rows3))
    if len(rows3) != 3:
        print("[FAIL] 文本解析条数不对"); ok = False

    # 6) 生成 LabRAD 脚本，并在 mock 设备命名空间里执行
    script = assign.build_labrad_script(rows, source=str(csv_path))
    (tmp / "generated_labrad.py").write_text(script, encoding="utf-8")
    ns2 = dict(make_namespace())
    ns2["__builtins__"] = __builtins__
    exec(compile(script, "generated_labrad.py", "exec"), ns2)
    got = ns2["_fread_updates"]["q21_5"]
    n_ok = len(ns2.get("_ok", []))
    print(f"[6] 生成脚本在 mock 设备上执行: 写入成功 {n_ok}/{len(rows)}，q21_5={got}")
    if abs(got - 6.556) > 1e-12:
        print("[FAIL] 生成脚本内容不对"); ok = False
    if n_ok != 9:
        print("[FAIL] 生成脚本未能全部写入"); ok = False
    if abs(ns2["q7_19"].regs.fread - 6.6215) > 1e-12:
        print("[FAIL] 生成脚本未更新 q7_19"); ok = False

    print("\n测试结果:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
