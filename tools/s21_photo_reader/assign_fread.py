# -*- coding: utf-8 -*-
"""把 S21 照片识别结果写入比特频率参数（fread）—— 走量子服务的 HTTP 端点。

用法：
    python tools/s21_photo_reader/assign_fread.py [读数CSV] [--base-url URL] [--dry-run]
    python tools/s21_photo_reader/assign_fread.py --base-url http://127.0.0.1:3003 --dry-run

写路径的唯一入口是服务端 `POST /qubit/set_params`（写闸门、串行锁、审计链都在服务端）；
本脚本不直连 LabRAD、不碰厂商对象，只负责「解析读数 → 发请求 → 回读校验」。

读数 CSV 省略时，自动取 `s21_image/` 目录里最新的一张结果 CSV。
"""

from __future__ import annotations

import argparse
import os
import sys

# 本文件所在目录 = <仓库根>/tools/s21_photo_reader（不写死绝对路径）
_TOOL_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(os.path.dirname(_TOOL_DIR))
if _TOOL_DIR not in sys.path:
    sys.path.insert(0, _TOOL_DIR)

from s21pr import assign  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="S21 读数 → 服务端 /qubit/set_params")
    ap.add_argument("csv", nargs="?", default=None, help="读数 CSV（默认取 s21_image/ 里最新的结果 CSV）")
    ap.add_argument("--base-url", default=assign.DEFAULT_BASE_URL, help="量子服务地址")
    ap.add_argument("--key", default="fread", help="要写的参数键（默认 fread）")
    ap.add_argument("--dry-run", action="store_true", help="只预览计划，不写硬件")
    ap.add_argument("--timeout", type=float, default=30.0, help="单次请求超时秒数")
    args = ap.parse_args(argv)

    path = args.csv
    if not path:
        found = assign.find_result_file(os.path.join(_REPO_ROOT, "s21_image"))
        if found is None:
            print("[ERR] 没找到读数 CSV；请先处理照片得到结果 CSV，或直接给出 CSV 路径",
                  file=sys.stderr)
            return 2
        path = str(found)
        print("自动选取最新结果文件: %s" % path)

    rows = assign.parse_assignments(path)
    print("读取 %d 条比特读数（来自 %s）" % (len(rows), path))
    for r in rows:
        print("    %-10s -> %s" % (r.qubit, r.value))

    if args.dry_run:
        print("\n--- 预览（不写入）---")
    results = assign.apply_fread(rows, base_url=args.base_url, dry_run=args.dry_run,
                                  key=args.key, timeout=args.timeout)
    print("\n" + assign.format_report(results))
    n_ok = sum(1 for r in results if r.ok)
    return 0 if n_ok == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
