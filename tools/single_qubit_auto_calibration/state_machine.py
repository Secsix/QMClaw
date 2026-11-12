from __future__ import annotations

from .models import CalibrationRun, CalibrationState


_ALLOWED = {
    CalibrationState.INIT: {CalibrationState.INPUT_VALIDATED, CalibrationState.FAILED},
    CalibrationState.INPUT_VALIDATED: {CalibrationState.CAVITY_CANDIDATES_FOUND, CalibrationState.NEEDS_REVIEW, CalibrationState.FAILED},
    CalibrationState.CAVITY_CANDIDATES_FOUND: {CalibrationState.ZPA_RANGE_FOUND, CalibrationState.NEEDS_REVIEW, CalibrationState.FAILED},
    CalibrationState.ZPA_RANGE_FOUND: {CalibrationState.SEARCHING_ACTIVE_POINT, CalibrationState.NEEDS_REVIEW, CalibrationState.FAILED},
    CalibrationState.SEARCHING_ACTIVE_POINT: {CalibrationState.ACTIVE_POINT_FOUND, CalibrationState.NEEDS_REVIEW, CalibrationState.FAILED},
    CalibrationState.ACTIVE_POINT_FOUND: {CalibrationState.F10_OPTIMIZED, CalibrationState.READOUT_OPTIMIZED, CalibrationState.PI_OPTIMIZED, CalibrationState.NEEDS_REVIEW},
    CalibrationState.F10_OPTIMIZED: {CalibrationState.READOUT_OPTIMIZED, CalibrationState.PI_OPTIMIZED, CalibrationState.NEEDS_REVIEW},
    CalibrationState.READOUT_OPTIMIZED: {CalibrationState.PI_OPTIMIZED, CalibrationState.FREQUENCY_TUNED, CalibrationState.NEEDS_REVIEW},
    CalibrationState.PI_OPTIMIZED: {CalibrationState.FREQUENCY_TUNED, CalibrationState.RELAXATION_VERIFIED, CalibrationState.NEEDS_REVIEW},
    CalibrationState.FREQUENCY_TUNED: {CalibrationState.RELAXATION_VERIFIED, CalibrationState.FINAL_VERIFIED, CalibrationState.NEEDS_REVIEW},
    CalibrationState.RELAXATION_VERIFIED: {CalibrationState.FINAL_VERIFIED, CalibrationState.NEEDS_REVIEW},
    CalibrationState.FINAL_VERIFIED: {CalibrationState.COMPLETE, CalibrationState.NEEDS_REVIEW},
}


class InvalidTransition(RuntimeError):
    pass


def transition(run: CalibrationRun, new_state: CalibrationState, reason: str = "") -> None:
    if new_state not in (CalibrationState.NEEDS_REVIEW, CalibrationState.FAILED) and new_state not in _ALLOWED.get(run.state, set()):
        raise InvalidTransition(f"{run.state.value} -> {new_state.value} is not allowed")
    run.transitions.append({"from": run.state.value, "to": new_state.value, "reason": reason})
    run.state = new_state
    if new_state == CalibrationState.NEEDS_REVIEW:
        run.failure = reason or "needs_review"
    if new_state == CalibrationState.FAILED:
        run.failure = reason or "failed"
