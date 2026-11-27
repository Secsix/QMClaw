"""写参逻辑的回归测试（用假量子服务代替真设备与真 HTTP）。

用法: python tests/test_assign.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from s21pr import assign            # noqa: E402


class FakeService:
    """假量子服务：只实现 /qubit/params 与 /qubit/set_params 两个端点。"""

    def __init__(self, initial):
        self.params = {q: {"fread": v} for q, v in initial.items()}
        self.calls = []

    def __call__(self, base_url, path, payload, timeout=30.0):
        self.calls.append((path, payload))
        if path == "/qubit/params":
            name = payload["name"]
            if name not in self.params:
                return {"error": f"Qubit {name} not found"}
            return {"name": name, "params": dict(self.params[name])}
        if path == "/qubit/set_params":
            name = payload["name"]
            if name not in self.params:
                return {"error": f"Qubit {name} not found"}
            self.params[name].update(payload["params"])
            return {"success": True, "name": name, "updated": list(payload["params"])}
        raise AssertionError(f"未预期的端点: {path}")

    def writes(self):
        return [c for c in self.calls if c[0] == "/qubit/set_params"]


INITIAL = {
    "q21_5": 6.40,
    "q19_7": 6.45,
    "q17_9": 6.39,
    "q15_11": 6.28,
    "q13_13": 6.35,
    "q11_15": 6.44,
    "q9_17": 6.50,
    "q7_19": 6.62,
    "q5_21": 6.54,
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
    svc = FakeService(INITIAL)
    orig_post = assign._post
    assign._post = svc                      # 假服务接管 HTTP 层

    try:
        # 1) 解析 CSV
        rows = assign.parse_assignments(csv_path)
        print(f"[1] 解析到 {len(rows)} 条: " +
              ", ".join(f"{r.qubit}={r.value}" for r in rows))
        if len(rows) != 9:
            print("[FAIL] 应为 9 条"); ok = False
        if abs(rows[0].value - 6.556) > 1e-9:
            print("[FAIL] 首条数值不对"); ok = False

        # 2) dry-run 不写硬件
        res = assign.apply_fread(rows, dry_run=True)
        if svc.writes():
            print("[FAIL] dry-run 却发了写请求"); ok = False
        if svc.params["q21_5"]["fread"] != 6.40:
            print("[FAIL] dry-run 却改了参数"); ok = False
        print(f"[2] dry-run: {sum(1 for r in res if r.ok)}/{len(res)} 预览，未发写请求 OK")

        # 3) 真实写入 + 回读
        res = assign.apply_fread(rows)
        good = sum(1 for r in res if r.ok)
        print(f"[3] 写入: {good}/{len(res)} 成功")
        if good != 9:
            print("[FAIL] 写入未全部成功")
            print(assign.format_report(res))
            ok = False
        if abs(svc.params["q21_5"]["fread"] - 6.556) > 1e-12:
            print("[FAIL] q21_5.fread 未更新"); ok = False
        if abs(svc.params["q5_21"]["fread"] - 6.5465) > 1e-12:
            print("[FAIL] q5_21.fread 未更新"); ok = False

        # 4) 不存在的比特要报错但不影响其它
        rows2 = assign.Assignment(qubit="q99_99", value=6.0), *rows
        res2 = assign.apply_fread(rows2)
        bad = [r for r in res2 if not r.ok]
        print(f"[4] 缺失比特处理: {len(bad)} 条失败（应为 1），其余 {len(res2)-len(bad)} 条成功")
        if len(bad) != 1 or "q99_99" not in bad[0].error:
            print("[FAIL] 缺失比特处理异常"); ok = False

        # 5) 文本格式（用户粘贴的赋值行）
        txt = tmp / "paste.txt"
        txt.write_text("q21_5.regs.fread = 6.556\nq19_7 6.469\nq17_9,6.393\n",
                       encoding="utf-8")
        rows3 = assign.parse_assignments(txt)
        print("[5] 文本解析: " + ", ".join(f"{r.qubit}={r.value}" for r in rows3))
        if len(rows3) != 3:
            print("[FAIL] 文本解析条数不对"); ok = False

        # 6) 生成服务写参请求清单
        payloads = assign.build_service_payloads(rows)
        print(f"[6] 写参请求清单: {len(payloads)} 条，首条 {payloads[0]}")
        if len(payloads) != 9:
            print("[FAIL] 请求清单条数不对"); ok = False
        if payloads[0]["name"] != "q21_5" or abs(payloads[0]["params"]["fread"] - 6.556) > 1e-12:
            print("[FAIL] 请求清单内容不对"); ok = False
        if abs(payloads[7]["params"]["fread"] - 6.6215) > 1e-12:
            print("[FAIL] 请求清单 q7_19 不对"); ok = False
    finally:
        assign._post = orig_post

    print("\n测试结果:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
