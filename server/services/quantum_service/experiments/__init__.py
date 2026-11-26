"""实验语义层：把"跑哪个实验"与"后端怎么实现"彻底分开。

对外只有两个概念：

* **kind** —— 语义名（``iqraw`` / ``t1`` / ``xeb`` / ``cz.dcz`` …），技能与智能体只认它；
* **backend** —— ``lqcs``（真机）/ ``qiskit``（模拟）/ ``offline_sim``（历史回放），
  由 ``BackendPolicy`` 决定用哪个，调用方不需要知道。

用法（服务内部）::

    from services.quantum_service.experiments import (
        CATALOG, ExperimentRequest, get_spec, catalog_json, run_experiment,
    )
"""

from .base import Backend, BackendError, ExperimentRequest, ExperimentResult
from .catalog import (
    BACKENDS,
    CATALOG,
    catalog_json,
    get_spec,
    supports,
    supported_kinds,
)
from .adapters import run_experiment, backend_status

__all__ = [
    "BACKENDS",
    "Backend",
    "BackendError",
    "CATALOG",
    "ExperimentRequest",
    "ExperimentResult",
    "backend_status",
    "catalog_json",
    "get_spec",
    "run_experiment",
    "supported_kinds",
    "supports",
]
