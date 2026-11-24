"""门面：后端适配器注册表 —— 实现见上游 `vendor/openqwf/adapters/`。

上游把适配器拆成**独立顶层包** `adapters/`（核心 `qwf/` 不反向依赖适配器），
注册表（`get_adapter` / `list_backends` / `describe` / `register`）在那份包里。
本仓保持原来的公开 API，实现直接用上游那份。

已注册两个后端：

* ``lqcs``   —— 真机（LabRAD）
* ``qiskit`` —— **转发**给宿主的 qiskit 服务（HTTP，`:3003/qiskit/*`）；
  本仓的 qiskit 实现在 `services/quantum_service/`，上游适配器只转发，不复刻。
"""

from __future__ import annotations

from .._upstream import upstream

_adapters = upstream("adapters")

BackendAdapter = _adapters.BackendAdapter
RunRequest = _adapters.RunRequest
RawResult = _adapters.RawResult
CancelToken = _adapters.CancelToken
CapabilityError = _adapters.CapabilityError
LqcsAdapter = _adapters.LqcsAdapter
QiskitAdapter = _adapters.QiskitAdapter
register = _adapters.register
get_adapter = _adapters.get_adapter
list_backends = _adapters.list_backends
describe = _adapters.describe

__all__ = [
    "BackendAdapter", "RunRequest", "RawResult", "CancelToken", "CapabilityError",
    "LqcsAdapter", "QiskitAdapter", "register", "get_adapter", "list_backends", "describe",
]
