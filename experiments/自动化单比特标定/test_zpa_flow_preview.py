"""test_zpa_flow_preview.py — 自动标定流程预览（默认离线，绝不碰真机）。

用法
----
    python test_zpa_flow_preview.py                      # 预览 q7_21 的完整流程
    python test_zpa_flow_preview.py --manifest m.json --bit q7_21
    python test_zpa_flow_preview.py --execute            # 真机执行（闸门须已打开）

打印的调用序列 = 真跑时**逐字**下发给 workflow_service 的内容（legacy 口径），
用来在开跑前确认流程；脚本本身不连服务、不写寄存器（--execute 除外）。
"""
from __future__ import annotations

import argparse
import json
import socket
from pathlib import Path

from classify_zpa_datavault import load_model as load_norm_model
from zpa_calibration_pipeline import (Settings, chain_steps, inspect_file,
                                      probe_steps, run_manifest)
from zpa_qwf_runner import default_budget, estimate_points

HERE = Path(__file__).resolve().parent
GATE_FILE = Path(r"D:\QMClaw\Agentic Workflow\qmclaw-server\config\fallback_config.json")


def show_step(item):
    args = json.dumps(item["args"], ensure_ascii=False)
    print(f"      {item['alias']:<4} {item['fn']:<16} {args}"
          f"   timeout={item['timeout']}s  ~{estimate_points(item)} 点")


def show_flow(manifest_path: Path, bit: str | None):
    cfg = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    settings = Settings(**cfg.get("settings", {}))
    settings.validate()
    entries = cfg["qubits"]
    entry = next((e for e in entries if bit is None or e["bit"] == bit), None)
    if entry is None:
        raise SystemExit(f"manifest 里没有比特 {bit}")

    print("=" * 78)
    print(f"目标比特：{entry['bit']}    路线：{settings.execution_route}")
    print(f"manifest：{manifest_path.name}    session：{cfg.get('adapter_config', {}).get('session')}")
    print("=" * 78)

    # ── 离线判定：区间 + bias 候选 ──────────────────────────────────
    model = load_norm_model()
    source = Path(entry["s21zpa_file"])
    info = inspect_file(source, settings, model)
    print(f"\n[离线判定] {source.name}")
    print(f"  文件正常性：{info['file_normal_prediction']}（score={info['file_normal_score']:.3f}）"
          f"  需复核：{info['file_review_required']}")
    print(f"  有效区间：{info.get('valid_zpa_range')}")
    for iv in info["intervals"]:
        print(f"    段 {iv['sampled_zpa_interval']}  {iv['point_count']} 层"
              f"  候选工作点 zpa={iv.get('candidate_zpa')}")
    print(f"  bias 候选（步长 {settings.bias_step} V）：{info['scan_bias_values']}")
    if not info["pipeline_eligible"]:
        print("  ⚠ 历史图不可用；但真机跑会**现场重采** S21ZPA，以现场那张为准")

    # ── 阶段 0/1/2 的调用序列 ──────────────────────────────────────
    biases = info["scan_bias_values"]
    print("\n[阶段 0] 现场 S21ZPA（真机第一步，update=False 只采不写）")
    zpa_step = {"alias": "z1", "fn": "sq.s21_zpa2d",
                "args": {"zpa": {"$arange": [settings.zpa_lo, settings.zpa_hi, settings.zpa_step]},
                         "freq_span": settings.zpa_freq_span, "update": False},
                "timeout": 1800}
    show_step(zpa_step)

    print("\n[阶段 1] bias 遍历探测（逐个 bias：写 bias_z → 等稳定 → 回读 → 三件套）")
    print(f"  候选 {len(biases)} 个；判据：visibility > {settings.visibility_threshold} 且 piamp 激发 → 取第一个通过的")
    for bias in biases[:3]:
        print(f"  -- bias_z={bias}")
        for item in probe_steps(settings, entry["bit"], bias):
            show_step(item)
    if len(biases) > 3:
        print(f"  -- 其余 {len(biases) - 3} 个 bias 同上（按顺序）")

    print("\n[阶段 2] 标定链（在选中的 bias 上一次下发）")
    chain = chain_steps(settings, entry["bit"], biases[0] if biases else -1.0)
    for item in chain:
        show_step(item)
    budget = default_budget(chain, "180m")
    print(f"  budget: {budget}")

    print("\n[收尾] 回读 bias_z 核对没被改 → 取指标 → 写结果 JSON/CSV → 关驱动")
    print("  指标：readout_fidelity / T1_us / T2_us(Ramsey T2*) / qubit_frequency_GHz")


def show_gates():
    print("\n[闸门与服务]")
    try:
        cfg = json.loads(GATE_FILE.read_text(encoding="utf-8-sig"))
        act = cfg["quantum_service"]["actuation"]
        print(f"  allow_execute={act.get('allow_execute')}  allow_set_params={act.get('allow_set_params')}"
              f"  writer_mode={act.get('writer_mode')}")
        mode = cfg["quantum_service"].get("offline_mode", {})
        print(f"  offline_mode={mode.get('default_mode')}（auto 时 LabRAD 掉线会**悄悄用历史数据**，"
              f"真机跑建议先切 online）")
    except Exception as exc:
        print(f"  读闸门配置失败：{exc}")
    for name, port in (("quantum_service", 3003), ("workflow_service", 3008)):
        with socket.socket() as s:
            s.settimeout(1.0)
            alive = s.connect_ex(("127.0.0.1", port)) == 0
        print(f"  {name} :{port} {'在线' if alive else '不在线'}")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--manifest", type=Path, default=HERE / "zpa_pipeline_manifest.example.json",
                    help="要预览的 manifest。默认用脱敏后的示例（实测 manifest 不入库）")
    ap.add_argument("--bit", default=None)
    ap.add_argument("--execute", action="store_true", help="真机执行（默认只预览）")
    ap.add_argument("--adapter", default="zpa_lab_adapter_template")
    args = ap.parse_args()

    show_flow(args.manifest.resolve(), args.bit)
    show_gates()
    if not args.execute:
        print("\n预览结束（未下发任何调用）。确认流程后加 --execute 真机跑。")
        return
    import importlib
    cfg = json.loads(args.manifest.read_text(encoding="utf-8-sig"))
    adapter = importlib.import_module(args.adapter).create_adapter(cfg.get("adapter_config", {}))
    results = run_manifest(args.manifest, adapter, execute=True, dry=False)
    print(json.dumps([{"bit": r["bit"], "status": r.get("status"),
                       "bias": r.get("selected_bias_z"), "metrics": r.get("metrics")}
                      for r in results], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
