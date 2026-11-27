"""DataVault 单比特体检 —— 量子分析服务客户端薄壳。

体检逻辑（读 DataVault HDF5、算指标、判活、生成 CSV / Markdown 报告）
已经下沉到 **analysis_service**：POST /health/report。
本脚本只负责发请求、把结果落成文件，不再自己读 HDF5、不再复刻指标口径。

用法：
    python health_report.py --vault "<vault>/<session>.dir" \
        --start 3648 --end 4455 -o out

输出：
    <out>/qubit_health.csv     每比特一行的指标表
    <out>/qubit_health.md      可读报告（含判活结论）
"""
from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_BASE_URL = "http://127.0.0.1:3004"


class ServiceError(RuntimeError):
    """分析服务返回了错误，或根本连不上。"""


def post(base_url: str, path: str, payload: dict, timeout: float = 600.0) -> dict:
    """POST 一个 JSON 到分析服务，返回解析后的 JSON。"""
    url = f"{base_url.rstrip('/')}{path}"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace") if exc.fp else ""
        raise ServiceError(f"{path} HTTP {exc.code}: {body.strip()[:400]}") from exc
    except urllib.error.URLError as exc:
        raise ServiceError(f"{path} 连不上 {base_url}: {exc.reason}") from exc
    try:
        out = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ServiceError(f"{path} 返回的不是 JSON: {raw[:200]}") from exc
    if isinstance(out, dict) and out.get("error"):
        raise ServiceError(f"{path} 失败: {out.get('error')}")
    return out


def fmt(v, nd=4):
    if v is None:
        return ""
    if isinstance(v, float):
        return f"{v:.{nd}g}"
    return str(v)


def main() -> int:
    ap = argparse.ArgumentParser(description="DataVault 单比特体检（走分析服务）")
    ap.add_argument("--vault", required=True, help="DataVault session 目录")
    ap.add_argument("--start", type=int, default=None, help="起始序号（含）")
    ap.add_argument("--end", type=int, default=None, help="结束序号（含）")
    ap.add_argument("-o", "--out", default="out", help="输出目录")
    ap.add_argument("--base-url", default=DEFAULT_BASE_URL, help="分析服务地址")
    ap.add_argument("--timeout", type=float, default=600.0, help="请求超时（秒）")
    args = ap.parse_args()

    payload = {"vault_dir": args.vault}
    if args.start is not None:
        payload["start"] = args.start
    if args.end is not None:
        payload["end"] = args.end

    try:
        resp = post(args.base_url, "/health/report", payload, timeout=args.timeout)
    except ServiceError as exc:
        print(f"体检失败: {exc}")
        return 1

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "qubit_health.csv").write_text(resp.get("csv", ""), encoding="utf-8-sig")
    (out / "qubit_health.md").write_text(resp.get("markdown", ""), encoding="utf-8")

    rows = resp.get("rows") or []
    print(f"比特数: {resp.get('count', len(rows))}   判活: {resp.get('alive', 0)}")
    for r in rows:
        print(f"  {r.get('qubit', ''):8s} iqraw_snr={fmt(r.get('iqraw_snr'), 3):>8s} "
              f"norm={fmt(r.get('iqraw_norm_best'), 3):>8s} "
              f"rabi_swing={fmt(r.get('rabi_contrast'), 3):>7s} "
              f"zpa={fmt(r.get('wp_zpa'), 4):>6s} f10={fmt(r.get('wp_f10'), 6):>9s} "
              f"f21={fmt(r.get('wp_f21'), 6):>9s} fread={fmt(r.get('wp_fread'), 6):>9s} "
              f"T2*={fmt(r.get('Ramsey_df_T2star'), 4):>9s} "
              f"{'ALIVE' if r.get('alive') else '-'}")
    print(f"\n报告: {out / 'qubit_health.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
