"""把 S21 读数写入 LabRAD 比特对象的 regs.fread。

纯标准库实现（可放进 LabRAD 内嵌 Python 运行）：
    from s21pr.assign import parse_assignments, apply_fread
    rows = parse_assignments(r"D:\\QMClaw\\s21_image\\读取线6上的待测比特.csv")
    apply_fread(rows)

支持的输入格式：
  1. 照片工具输出的 CSV（列 qubit / s21_dip_value，或 qubit / frequency_GHz）
  2. 纯文本，每行 "q21_5 6.556" / "q21_5,6.556" / "q21_5.regs.fread = 6.556"
"""
from __future__ import annotations

import csv
import os
import re
from dataclasses import dataclass, field
from pathlib import Path


# ------------------------------------------------------------------ 数据结构
@dataclass
class Assignment:
    qubit: str
    value: float
    source: str = ""
    note: str = ""

    def as_line(self, key: str = "fread") -> str:
        return f"{self.qubit}.regs.{key} = {self.value:g}"


@dataclass
class Result:
    qubit: str
    value: float
    ok: bool = False
    old: object = None
    new: object = None
    error: str = ""
    source: str = ""
    note: str = ""


# ------------------------------------------------------------------ 解析
QUBIT_COL_NAMES = ("qubit", "比特号", "name", "qubit_name")
VALUE_COL_NAMES = ("s21_dip_value", "frequency_ghz", "dip_value", "s21_value",
                   "value", "波谷值", "freq_ghz")
LINE_RE = re.compile(
    r"(?P<name>[A-Za-z_][\w]*(?:_\d+)?)\s*(?:\.\s*regs\s*\.\s*(?P<key>\w+))?\s*"
    r"[=,\s]\s*(?P<val>[+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)")


def _pick(headers, candidates):
    low = {h.strip().lower(): h for h in headers if h}
    for c in candidates:
        if c in low:
            return low[c]
    return None


def parse_csv_file(path) -> list[Assignment]:
    path = Path(path)
    out = []
    # utf-8-sig：照片工具写出的 CSV 带 BOM
    with open(path, "r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        headers = reader.fieldnames or []
        qcol = _pick(headers, QUBIT_COL_NAMES)
        vcol = _pick(headers, VALUE_COL_NAMES)
        if qcol is None or vcol is None:
            raise ValueError(
                f"{path.name}: 找不到比特列/数值列（表头={headers}）")
        for row in reader:
            q = (row.get(qcol) or "").strip()
            v = (row.get(vcol) or "").strip()
            if not q or not v:
                continue
            try:
                val = float(v)
            except ValueError:
                continue
            unit = (row.get("unit") or "").strip()
            out.append(Assignment(qubit=q, value=val, source=path.name,
                                  note=f"单位 {unit}" if unit else ""))
    return out


def parse_text_file(path) -> list[Assignment]:
    path = Path(path)
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = LINE_RE.search(line)
        if not m:
            continue
        try:
            val = float(m.group("val"))
        except ValueError:
            continue
        out.append(Assignment(qubit=m.group("name"), value=val, source=path.name))
    return out


def parse_assignments(path) -> list[Assignment]:
    """按扩展名/内容自动选择解析器。"""
    path = Path(path)
    if path.suffix.lower() == ".csv":
        return parse_csv_file(path)
    if path.suffix.lower() in (".txt", ".list", ".dat", ""):
        try:
            rows = parse_text_file(path)
            if rows:
                return rows
        except OSError:
            pass
    return parse_csv_file(path)


def find_result_file(folder, prefer_stem: str | None = None) -> Path | None:
    """在目录里找照片工具的输出 CSV（跳过 summary 汇总表）。"""
    folder = Path(folder)
    if not folder.is_dir():
        return None
    cands = [p for p in folder.glob("*.csv")
             if "summary" not in p.stem.lower() and p.stem.lower() != "s21_dips_r6"]
    if prefer_stem:
        exact = folder / f"{prefer_stem}.csv"
        if exact.exists():
            return exact
    if not cands:
        return None
    return max(cands, key=lambda p: p.stat().st_mtime)


# ------------------------------------------------------------------ 写入 LabRAD
def _resolve(namespace, name):
    """在命名空间里找比特对象（LabRAD 笔记里设备名是全局变量）。"""
    if namespace is None:
        namespace = globals()
    obj = namespace.get(name) if hasattr(namespace, "get") else None
    if obj is None:
        try:
            obj = eval(name, namespace)      # LabRAD: eval("<qname>", ns)
        except Exception:
            obj = None
    return obj


def apply_fread(assignments, namespace=None, dry_run: bool = False,
                verify: bool = True, key: str = "fread",
                tol: float = 1e-6) -> list[Result]:
    """把每个比特的频率写入 regs.<key>。

    namespace：比特对象所在的命名空间（默认本模块 globals；
    在 LabRAD 里请传**调用方**的 globals，否则找不到比特设备）。
    dry_run=True 时只回报计划，不写硬件。
    返回逐条结果（成功/失败/旧值/新值）。
    """
    results = []
    for a in assignments:
        r = Result(qubit=a.qubit, value=a.value, source=a.source, note=a.note)
        obj = _resolve(namespace, a.qubit)
        if obj is None:
            r.error = f"命名空间里找不到比特对象 {a.qubit}"
            results.append(r)
            continue
        regs = getattr(obj, "regs", None)
        if regs is None:
            r.error = f"{a.qubit} 没有 .regs 属性"
            results.append(r)
            continue
        try:
            r.old = getattr(regs, key, None)
        except Exception as e:                      # 读寄存器可能抛异常
            r.old = f"<读取失败: {e}>"
        if dry_run:
            r.ok = True
            r.new = a.value
            r.note = (r.note + " | 仅预览，未写入").strip(" |")
            results.append(r)
            continue
        try:
            setattr(regs, key, a.value)
        except Exception as e:
            r.error = f"写入失败: {type(e).__name__}: {e}"
            results.append(r)
            continue
        if verify:
            try:
                r.new = getattr(regs, key)
                if r.new is None or abs(float(r.new) - a.value) > tol:
                    r.error = f"回读不一致: 期望 {a.value}，实际 {r.new}"
                    results.append(r)
                    continue
            except Exception as e:
                r.error = f"回读失败: {type(e).__name__}: {e}"
                results.append(r)
                continue
        else:
            r.new = a.value
        r.ok = True
        results.append(r)
    return results


# ------------------------------------------------------------------ 生成脚本
def build_labrad_script(assignments, key: str = "fread", title: str = "",
                         source: str = "") -> str:
    """生成一段可直接粘进 LabRAD 的赋值脚本（自包含，不依赖本仓库）。"""
    lines = [
        "# -*- coding: utf-8 -*-",
        "# 在 LabRAD 中粘贴并执行：把 S21 照片识别结果写入各比特 regs.%s" % key,
    ]
    if title:
        lines.append(f"# 数据来源：{title}")
    if source:
        lines.append(f"# 读数文件：{source}")
    lines.append("")
    lines.append("_fread_updates = {")
    for a in assignments:
        lines.append(f"    {a.qubit!r}: {a.value!r},   # {a.note}".rstrip())
    lines.append("}")
    lines.append("")
    lines.append("_ok, _fail = [], []")
    lines.append("for _q, _v in _fread_updates.items():")
    lines.append("    try:")
    lines.append(f"        _obj = globals()[_q]")
    lines.append(f"        _old = getattr(_obj.regs, {key!r}, None)")
    lines.append(f"        setattr(_obj.regs, {key!r}, _v)")
    lines.append(f"        _new = getattr(_obj.regs, {key!r}, None)")
    lines.append("        if _new is None or abs(float(_new) - _v) > 1e-6:")
    lines.append("            _fail.append((_q, _v, '回读不一致: %s' % _new))")
    lines.append("        else:")
    lines.append("            _ok.append((_q, _old, _v))")
    lines.append("    except Exception as _e:")
    lines.append("        _fail.append((_q, _v, '%s: %s' % (type(_e).__name__, _e)))")
    lines.append("")
    lines.append("print('=== 写入成功 %d 条 ===' % len(_ok))")
    lines.append("for _q, _old, _v in _ok:")
    lines.append("    print('  %-10s %s -> %s' % (_q, _old, _v))")
    lines.append("if _fail:")
    lines.append("    print('=== 失败 %d 条 ===' % len(_fail))")
    lines.append("    for _q, _v, _err in _fail:")
    lines.append("        print('  %-10s %s  (%s)' % (_q, _v, _err))")
    lines.append("")
    return "\n".join(lines)


def format_report(results) -> str:
    out = ["{:<10s} {:>12s} {:>12s} {:>12s}  {}".format(
        "比特", "原值", "新值", "期望", "状态")]
    for r in results:
        status = "OK" if r.ok else ("预览" if r.error == "" else "失败")
        out.append("{:<10s} {:>12} {:>12} {:>12}  {}".format(
            r.qubit, r.old if r.old is not None else "-",
            r.new if r.new is not None else "-", r.value, status))
        if r.error and r.error != "仅预览，未写入":
            out.append("    ↳ {}".format(r.error))
    n_ok = sum(1 for r in results if r.ok)
    out.append(f"合计 {n_ok}/{len(results)} 成功")
    return "\n".join(out)
