"""把 S21 读数写入比特频率参数（fread）。

写入走量子服务（quantum_service）的 HTTP 端点，是本仓唯一的 sanctioned
写路径 —— 服务端负责写闸门、串行锁与审计链：

    读旧值: POST /qubit/params     {"name": "q21_5"}
    写参数: POST /qubit/set_params {"name": "q21_5", "params": {"fread": 6.556}}

本模块只做「读数解析 + 发请求 + 回读校验」，不直连 LabRAD、不碰厂商对象。
纯标准库实现：
    from s21pr.assign import parse_assignments, apply_fread
    rows = parse_assignments(r"<仓库根>\\s21_image\\读取线6上的待测比特.csv")
    apply_fread(rows, base_url="http://127.0.0.1:3003")

支持的输入格式：
  1. 照片工具输出的 CSV（列 qubit / s21_dip_value，或 qubit / frequency_GHz）
  2. 纯文本，每行 "q21_5 6.556" / "q21_5,6.556" / "q21_5.regs.fread = 6.556"
"""
from __future__ import annotations

import csv
import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path


# 量子服务默认地址（与 tools/iqraw_contrast_tune.py 保持一致）
DEFAULT_BASE_URL = "http://127.0.0.1:3003"


# ------------------------------------------------------------------ 数据结构
@dataclass
class Assignment:
    qubit: str
    value: float
    source: str = ""
    note: str = ""

    def as_line(self, key: str = "fread") -> str:
        return f"{self.qubit}.{key} = {self.value:g}"


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


# ------------------------------------------------------------------ 服务端写参
class ServiceError(RuntimeError):
    """服务端拒绝、报错或不可达。"""


def _post(base_url: str, path: str, payload: dict, timeout: float = 30.0) -> dict:
    """向量子服务发一个 JSON POST，返回解析后的响应体。"""
    url = f"{base_url.rstrip('/')}{path}"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        raise ServiceError(f"{path} HTTP {e.code}: {raw[:400]}") from e
    except urllib.error.URLError as e:
        raise ServiceError(f"{path} 连不上 {base_url}: {e.reason}") from e
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        raise ServiceError(f"{path} 返回非 JSON: {raw[:200]}") from None


def read_param(base_url: str, qubit: str, key: str, timeout: float = 30.0):
    """读比特参数旧值（POST /qubit/params）；读不到返回 None。"""
    body = _post(base_url, "/qubit/params", {"name": qubit}, timeout)
    if body.get("error"):
        raise ServiceError(str(body["error"]))
    params = body.get("params") or {}
    return params.get(key)


def set_param(base_url: str, qubit: str, key: str, value: float,
              timeout: float = 30.0) -> dict:
    """写比特参数（POST /qubit/set_params，受服务端写闸门约束）。"""
    body = _post(base_url, "/qubit/set_params",
                 {"name": qubit, "params": {key: value}}, timeout)
    if body.get("error") or not body.get("success"):
        raise ServiceError(str(body.get("error") or body.get("hint") or "服务端未确认写入成功"))
    return body


def build_service_payloads(assignments, key: str = "fread") -> list:
    """产出 /qubit/set_params 请求体清单（人工核对或交给服务批量执行）。"""
    return [{"name": a.qubit, "params": {key: a.value}} for a in assignments]


def apply_fread(assignments, base_url: str = DEFAULT_BASE_URL, dry_run: bool = False,
                verify: bool = True, key: str = "fread", tol: float = 1e-6,
                timeout: float = 30.0) -> list:
    """把每个比特的读数写入 <key>（默认 fread）。

    写路径唯一入口是服务端的 /qubit/set_params（写闸门、串行锁、审计链都在服务端）；
    本模块不直连 LabRAD、不碰厂商对象。
    dry_run=True 时只回报计划，不写硬件。
    返回逐条结果（成功/失败/旧值/新值）。
    """
    results = []
    for a in assignments:
        r = Result(qubit=a.qubit, value=a.value, source=a.source, note=a.note)
        try:
            r.old = read_param(base_url, a.qubit, key, timeout)
        except ServiceError as e:
            r.old = f"<读取失败: {e}>"
        if dry_run:
            r.ok = True
            r.new = a.value
            r.note = (r.note + " | 仅预览，未写入").strip(" |")
            results.append(r)
            continue
        try:
            set_param(base_url, a.qubit, key, a.value, timeout)
        except ServiceError as e:
            r.error = f"写入失败: {e}"
            results.append(r)
            continue
        if verify:
            try:
                r.new = read_param(base_url, a.qubit, key, timeout)
                if r.new is None or abs(float(r.new) - a.value) > tol:
                    r.error = f"回读不一致: 期望 {a.value}，实际 {r.new}"
                    results.append(r)
                    continue
            except ServiceError as e:
                r.error = f"回读失败: {e}"
                results.append(r)
                continue
        else:
            r.new = a.value
        r.ok = True
        results.append(r)
    return results


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
