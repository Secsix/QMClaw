"""qwf_sweep.py — 把 `qwf_scripts/` 里的实验逐个跑一遍（薄壳）。

**历史**：这个脚本原来自己拼 .qwf 文本（内嵌一张 EXPS 参数表），和
`qwf_scripts/` 各说各话，两边参数一漂就不知道该信谁。现在脚本本体统一放在
`qwf_scripts/*.qwf`（每份一个实验），这里只负责"按顺序跑 + 汇总"。

用法：
    python qwf_sweep.py                      # 只打印计划（默认不跑）
    python qwf_sweep.py --if yes             # 真跑全部
    python qwf_sweep.py --only t1 ramsey     # 只跑指定实验
    python qwf_sweep.py --if yes --out DIR   # 汇总写 DIR/summary.json

等价于 `python qwf_exp.py --all [--if yes]`。
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

import qwf_exp

ROOT = Path(__file__).resolve().parent


def main() -> int:
    ap = argparse.ArgumentParser(description="跑 qwf_scripts/ 里的实验（薄壳，见 qwf_exp.py）")
    ap.add_argument("--only", nargs="*", default=[], help="只跑指定实验名")
    ap.add_argument("--if", dest="confirm", default="", help="真跑要 --if yes")
    ap.add_argument("--dry", action="store_true", help="干跑：不碰硬件")
    ap.add_argument("--base", default=qwf_exp.WF_BASE, help="workflow_service 地址")
    ap.add_argument("--poll", type=int, default=120, help="每个实验轮询秒数")
    ap.add_argument("--out", default=str(Path(tempfile.gettempdir()) / "qmclaw_qiskit_sweep"),
                    help="汇总输出目录")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    argv = ["--base", args.base, "--poll", str(args.poll)]
    if args.dry:
        argv.append("--dry")
    if args.confirm:
        argv += ["--if", args.confirm]
    if args.verbose:
        argv.append("--verbose")
    if args.only:
        argv += args.only
    else:
        argv.append("--all")

    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)
    argv += ["--out", str(outdir / "summary.json")]

    return qwf_exp.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
