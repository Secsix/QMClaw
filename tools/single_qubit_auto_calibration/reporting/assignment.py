from __future__ import annotations

from ..safety import build_assignment_plan


def make_plan(run):
    final = run.final
    params = {} if final is None else {"fread": final.fread, "f10": final.f10,
                                       "f21": final.f21, "bias_z": final.zpa}
    return build_assignment_plan(run.input.qubit_id, params, run.config)
