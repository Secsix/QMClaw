from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import load_config
from .models import QubitInput
from .persistence.run_store import save_run
from .reporting.report import write_report


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Offline-first single-qubit calibration")
    p.add_argument("command", choices=["plan", "report"])
    p.add_argument("--qubit", required=True)
    p.add_argument("--fread-ref", type=float, required=True)
    p.add_argument("--zpa-min", type=float, default=-1.0)
    p.add_argument("--zpa-max", type=float, default=3.0)
    p.add_argument("--out", default="calibration_run")
    args = p.parse_args(argv)
    cfg = load_config()
    inp = QubitInput(args.qubit, args.fread_ref, args.zpa_min, args.zpa_max)
    if args.command == "plan":
        Path(args.out).mkdir(parents=True, exist_ok=True)
        Path(args.out, "plan.json").write_text(json.dumps({"input": inp.__dict__, "config": cfg.__dict__}, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(f"计划已生成: {Path(args.out, 'plan.json')}")
        return 0
    raise SystemExit("report command requires a run object; use workflow API")


if __name__ == "__main__":
    raise SystemExit(main())
