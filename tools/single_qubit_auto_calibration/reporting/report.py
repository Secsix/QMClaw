from __future__ import annotations

import json
from pathlib import Path

from ..safety import build_assignment_plan


def assignment_script(qubit_id: str, params: dict, path: str | Path) -> None:
    lines = ["# Generated plan only; review before executing in LabRAD.", f"_qubit = {qubit_id!r}", "_updates = {"]
    lines.extend(f"    {k!r}: {float(v)!r}," for k, v in params.items() if v is not None)
    lines.extend(["}", "# No hardware write is performed by this file."])
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_report(run, outdir: str | Path) -> dict:
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    payload = run.to_dict()
    (out / "run.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    final = run.final
    params = {}
    if final:
        params = {"fread": final.fread, "f10": final.f10, "f21": final.f21, "bias_z": final.zpa}
    plan = build_assignment_plan(run.input.qubit_id, params, run.config)
    (out / "assignment_plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    assignment_script(run.input.qubit_id, plan["params"], out / "assignment_plan.py")
    lines = [f"# Single-qubit calibration report: {run.input.qubit_id}", "", f"状态: {run.state.value}", ""]
    if run.failure:
        lines += [f"失败/需复核: {run.failure}", ""]
    lines += ["## 候选工作点", "", "| zpa | fread | f10 | score |", "|---:|---:|---:|---:|"]
    for c in run.candidates:
        lines.append(f"| {c.zpa:.6g} | {c.fread if c.fread is not None else ''} | {c.f10 if c.f10 is not None else ''} | {c.score:.4g} |")
    lines += ["", "## 安全说明", "", "参数计划默认只读，未执行硬件写入；需人工审核后使用。"]
    (out / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return plan
