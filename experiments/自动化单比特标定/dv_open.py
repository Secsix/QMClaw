"""dv_open.py — 打开 DataVault HDF5 的统一入口（只读）。

为什么需要它
------------
LabRAD 写完一个数据集后，**文件句柄还会被独占一小会儿**（实测 20~30 s），
这时任何读取都会失败：

* `h5py.File(path, 'r')` → ``OSError: unable to lock file``（HDF5 自己的锁）
* `h5py.File(path, 'r', locking=False)` → ``PermissionError: [Errno 13]``
  （Windows 上 LabRAD 没给读共享）

所以凡是"刚写完就要读"的路径（现场 S21ZPA、刚做完的 IQraw / PiPulse），
必须 **locking=False + 重试等待**，否则流水线会正好死在刚采完的那一步
（2026-11-13 q21_5 真机首跑就是这么失败的：阶段 0 采完 dataset 180，
紧接着读文件 → PermissionError → status=error）。
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Optional

import h5py

DEFAULT_ATTEMPTS = 60        # 60 × 3 s = 180 s
DEFAULT_WAIT = 3.0


def open_datavault(path, attempts: int = DEFAULT_ATTEMPTS,
                   wait: float = DEFAULT_WAIT, **kwargs: Any):
    """只读打开 DataVault 文件；文件还被 LabRAD 占着就等它释放。

    Args:
        path: HDF5 路径（DataVault 数据集文件）。
        attempts: 最多尝试次数。
        wait: 每次失败后的等待秒数。
        **kwargs: 透传给 `h5py.File`（如 `swmr=True`）。

    Returns:
        已打开的 `h5py.File`（调用方负责 close）。

    Raises:
        OSError: 一直打不开时抛出最后一次的异常（PermissionError 是其子类）。
    """
    last: Optional[BaseException] = None
    attempts = max(1, int(attempts))
    for index in range(attempts):
        try:
            return h5py.File(str(Path(path)), "r", locking=False, **kwargs)
        except OSError as exc:          # PermissionError 也是 OSError
            last = exc
            if index + 1 < attempts:
                time.sleep(wait)
    raise last if last is not None else OSError(f"cannot open {path}")
