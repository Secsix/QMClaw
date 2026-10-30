# -*- coding: utf-8 -*-
"""在 LabRAD 中把 S21 照片识别结果写入各比特的 regs.fread。

用法 A（推荐，LabRAD 笔记里执行）：
    exec(open(r"D:\\QMClaw\\tools\\s21_photo_reader\\labrad_assign_fread.py").read())

用法 B（直接在 LabRAD 里粘贴赋值行）：
    q21_5.regs.fread = 6.556
    ...

用法 C（用本仓库的通用实现，读 CSV 后批量赋值）：
    import sys
    sys.path.insert(0, r"D:\\QMClaw\\tools\\s21_photo_reader")
    from s21pr.assign import parse_assignments, apply_fread, format_report
    rows = parse_assignments(r"D:\\QMClaw\\s21_image\\读取线6上的待测比特.csv")
    print(format_report(apply_fread(rows)))

说明：
  * 比特对象在 LabRAD 命名空间里是全局变量（q21_5 等），通过 globals() 取；
  * 写入后逐条回读校验，失败会单独列出，不会中断其它比特；
  * 只写 regs.fread，不动 f10 / bias_z 等其它寄存器。
"""

import os
import sys

_REPO_TOOL_DIR = r"D:\QMClaw\tools\s21_photo_reader"
if _REPO_TOOL_DIR not in sys.path:
    sys.path.insert(0, _REPO_TOOL_DIR)

# 默认读数文件：改成你本次照片处理后的 CSV 路径
CSV_PATH = r"D:\QMClaw\s21_image\读取线6上的待测比特.csv"
# 也支持留空 = 自动取目录里最新的一张结果 CSV
CSV_PATH = os.environ.get("S21PR_RESULT_CSV", CSV_PATH)

DRY_RUN = False        # True = 只打印计划，不写硬件


def _latest_csv(folder=r"D:\QMClaw\s21_image"):
    from s21pr.assign import find_result_file
    return find_result_file(folder)


def main(csv_path=None, dry_run=None, namespace=None):
    from s21pr.assign import (apply_fread, format_report,
                               find_result_file, parse_assignments)

    # 比特对象在"调用方"的命名空间里（LabRAD 笔记 / 粘贴执行的脚本），
    # 所以优先用调用者的 globals，而不是本模块自己的 globals。
    if namespace is None:
        try:
            import inspect
            namespace = inspect.currentframe().f_back.f_globals
        except Exception:
            namespace = None

    path = csv_path or CSV_PATH
    if not path or not os.path.isfile(path):
        found = find_result_file(r"D:\QMClaw\s21_image")
        if found is None:
            print("[ERR] 没找到读数 CSV；请先把照片用 s21_photo_reader 处理出 CSV，"
                  "或修改本脚本里的 CSV_PATH")
            return []
        path = str(found)
        print("自动选取最新结果文件: %s" % path)

    rows = parse_assignments(path)
    print("读取 %d 条比特读数（来自 %s）" % (len(rows), path))
    for r in rows:
        print("    %-10s -> %s" % (r.qubit, r.value))

    use_dry = DRY_RUN if dry_run is None else dry_run
    if use_dry:
        print("\n--- 预览（不写入）---")
    results = apply_fread(rows, namespace=namespace, dry_run=use_dry)
    print("\n" + format_report(results))
    return results


if __name__ == "__main__":
    main()
