from __future__ import annotations

from typing import Any

from .analysis.quality_gate import final_pass, iq_fast_pass, rabi_pass
from .analysis.strategy import score_candidate
from .experiments.runner import run_experiment
from .models import CalibrationRun, CalibrationState, CandidateWorkingPoint, ExperimentResult, QubitInput
from .state_machine import transition


class CalibrationWorkflow:
    """Offline-first workflow. Adapter may be MockAdapter or an explicitly enabled real adapter."""

    def __init__(self, adapter: Any, config):
        self.adapter = adapter
        self.config = config

    def _record(self, run: CalibrationRun, result: ExperimentResult) -> ExperimentResult:
        run.results.append(result)
        return result

    def run(self, inp: QubitInput, qubit: Any = None) -> CalibrationRun:
        run = CalibrationRun(inp, self.config)
        try:
            if not inp.qubit_id:
                transition(run, CalibrationState.FAILED, "missing_qubit_id")
                return run
            transition(run, CalibrationState.INPUT_VALIDATED, "input_validated")
            if inp.fread_ref is None:
                transition(run, CalibrationState.NEEDS_REVIEW, "missing_fread_reference")
                return run

            s21 = self._record(run, run_experiment(self.adapter, "s21", qubit,
                fread_ref=inp.fread_ref, half_span=self.config.s21_half_span,
                points=self.config.s21_points, update=False, do_plot=False))
            fread = s21.metrics.get("fread_best") or s21.metrics.get("fread_amp0") or inp.fread_ref
            if not s21.ok and not fread:
                transition(run, CalibrationState.NEEDS_REVIEW, "NO_CAVITY")
                return run
            transition(run, CalibrationState.CAVITY_CANDIDATES_FOUND, "s21_candidate")

            zpa_min = inp.zpa_min if inp.zpa_min is not None else -1.0
            zpa_max = inp.zpa_max if inp.zpa_max is not None else 3.0
            transition(run, CalibrationState.ZPA_RANGE_FOUND, "zpa_range_configured")
            transition(run, CalibrationState.SEARCHING_ACTIVE_POINT, "zpa_scan_started")
            for zpa in self._grid(zpa_min, zpa_max, self.config.zpa_step):
                spec = self._record(run, run_experiment(self.adapter, "spectroscopy", qubit,
                    zpa=zpa, fread=fread, update=False, do_plot=False))
                if not spec.ok:
                    continue
                f10 = spec.metrics.get("f10")
                iq = self._record(run, run_experiment(self.adapter, "iqraw", qubit,
                    zpa=zpa, f10=f10, fread=fread, do_plot=False))
                if not iq.ok or not iq_fast_pass(iq.metrics, self.config):
                    continue
                rabi = self._record(run, run_experiment(self.adapter, "piamp", qubit,
                    zpa=zpa, f10=f10, amp=self.config.piamp_span, update=False, do_plot=False))
                if not rabi.ok:
                    continue
                metrics = {"iq": iq.metrics, "rabi": rabi.metrics}
                candidate = CandidateWorkingPoint(inp.qubit_id, zpa, fread=fread, f10=f10,
                                                  metrics=metrics, score=score_candidate(metrics, self.config))
                run.candidates.append(candidate)
                if rabi_pass(rabi.metrics, self.config) and self.config.early_stop:
                    run.final = candidate
                    break
            if run.final is None and run.candidates:
                run.final = max(run.candidates, key=lambda c: c.score)
            if run.final is None:
                transition(run, CalibrationState.NEEDS_REVIEW, "NO_ACTIVE_POINT")
                return run
            transition(run, CalibrationState.ACTIVE_POINT_FOUND, "active_candidate")

            # Strategy actions are safe, update=False measurements. They do not write hardware.
            if not run.final.f10:
                transition(run, CalibrationState.NEEDS_REVIEW, "missing_f10")
                return run
            transition(run, CalibrationState.F10_OPTIMIZED, "spectroscopy_result")
            readout = self._record(run, run_experiment(self.adapter, "s21_dis", qubit,
                zpa=run.final.zpa, f10=run.final.f10, update=False, do_plot=False))
            if readout.metrics.get("fread_best") is not None:
                run.final.fread = readout.metrics["fread_best"]
            transition(run, CalibrationState.READOUT_OPTIMIZED, "readout_measured")
            pi = self._record(run, run_experiment(self.adapter, "piamp", qubit,
                zpa=run.final.zpa, f10=run.final.f10, amp=self.config.piamp_span,
                update=False, do_plot=False))
            run.final.metrics["final_piamp"] = pi.metrics
            transition(run, CalibrationState.PI_OPTIMIZED, "piamp_verified")
            ramsey = self._record(run, run_experiment(self.adapter, "ramsey_df", qubit,
                zpa=run.final.zpa, f10=run.final.f10, update=False, do_plot=False))
            run.final.metrics["ramsey"] = ramsey.metrics
            transition(run, CalibrationState.FREQUENCY_TUNED, "ramsey_measured")
            t1 = self._record(run, run_experiment(self.adapter, "t1", qubit,
                zpa=run.final.zpa, do_plot=False))
            run.final.metrics["t1"] = t1.metrics
            transition(run, CalibrationState.RELAXATION_VERIFIED, "t1_measured")
            iq_m = run.final.metrics.get("iq", {})
            rabi_m = run.final.metrics.get("rabi", {})
            if final_pass(iq_m, rabi_m, ramsey.metrics, t1.metrics.get("T1_us"), self.config):
                transition(run, CalibrationState.FINAL_VERIFIED, "quality_gate_pass")
                transition(run, CalibrationState.COMPLETE, "complete")
            else:
                transition(run, CalibrationState.NEEDS_REVIEW, "final_quality_gate_failed")
            return run
        except Exception as exc:
            transition(run, CalibrationState.FAILED, f"unexpected:{exc}")
            return run

    @staticmethod
    def _grid(lo: float, hi: float, step: float):
        n = int(round((hi - lo) / step))
        return [round(lo + i * step, 12) for i in range(n + 1)]
