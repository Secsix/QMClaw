"""DataVault（LabRAD HDF5）离线读取工具。

只依赖 h5py + numpy，可直接读 D:\\DataVault\\<session>.dir 下的数据文件。

文件命名：  "03649 - q21_5%c IQraw.hdf5"
            └序号 └比特名 └实验名

HDF5 结构：
  /DataVault          结构化数组，字段 f0..fN（f0 = 独立变量，其余 = 因变量）
  /DataVault.attrs    Independent<i>.label / Dependent<i>.label  → 列名
                      Param.xxx                                → 测量时的参数快照
                      Access/Creation Time                      → 时间戳
"""
from __future__ import annotations

import base64
import re
import struct
from dataclasses import dataclass, field
from pathlib import Path

import h5py
import numpy as np

# "03649 - q21_5%c IQraw.hdf5"  →  序号 3649 / 比特 q21_5 / 实验 IQraw
NAME_RE = re.compile(r"^(?P<idx>\d+)\s*-\s*(?P<qubit>\S+)\s+(?P<exp>.+)$")


def parse_name(path: Path):
    stem = path.stem.replace("%c", " ").strip()
    m = NAME_RE.match(stem)
    if not m:
        return None
    return int(m.group("idx")), m.group("qubit").strip(), m.group("exp").strip()


# ------------------------------------------------------------------ 参数解码
def _b64(s) -> bytes | None:
    if isinstance(s, (bytes, bytearray)):
        s = s.decode("utf-8", "replace")
    s = str(s)
    if "base64," not in s:
        return None
    body = s.split("base64,", 1)[1].strip()
    try:
        return base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
    except Exception:
        return None


def decode_labrad_value(raw: bytes):
    """解码 LabRAD tagged data（data URL 里的 base64 部分）。

    布局：[4 字节头][1 字节 tag][payload]
      'i' int32   : payload = int32 长度(4) + 4 字节值
      's' string  : payload = int32 字节数 + int32 字符数 + utf8
      'v' float64 : payload = 6 字节(长度等) + **大端** float64
      '*' complex : payload = int32 字节数 + int32 个数 + 数据
    """
    if raw is None or len(raw) < 5:
        return None
    tag = raw[4:5]
    try:
        if tag == b"v" and len(raw) >= 19:
            return struct.unpack(">d", raw[11:19])[0]
        if tag == b"i" and len(raw) >= 13:
            return struct.unpack(">i", raw[9:13])[0]
        if tag == b"s" and len(raw) >= 13:
            return raw[13:].decode("utf-8", "replace")
        if tag == b"*":
            if len(raw) >= 17:
                nbytes, count = struct.unpack(">ii", raw[5:13])
                if count == 1 and nbytes == 8 and len(raw) >= 21:
                    re_, im_ = struct.unpack(">ii", raw[13:21])
                    return complex(re_, im_)
            return None
    except Exception:
        return None
    return None


def _flatten(prefix: str, node, out: dict):
    """把 parse_parameters 那种嵌套字典压平成 {'q21_5.f10': 值}。"""
    if isinstance(node, dict):
        for k, v in node.items():
            _flatten(f"{prefix}{k}.", v, out)
    else:
        out[prefix.rstrip(".")] = node


# ------------------------------------------------------------------ 数据集
@dataclass
class Dataset:
    index: int
    qubit: str
    exp: str
    path: Path
    columns: dict = field(default_factory=dict)      # 列名 -> np.ndarray
    params: dict = field(default_factory=dict)       # 'q21_5.f10' -> 值
    attrs: dict = field(default_factory=dict)        # 原始标签/时间戳
    creation_time: float = 0.0

    def param(self, name: str, default=None):
        """按短名取值：param('f10') 会先找 '<qubit>.f10'。"""
        if name in self.params:
            return self.params[name]
        cand = f"{self.qubit}.{name}"
        if cand in self.params:
            return self.params[cand]
        return default

    def qubit_params(self) -> dict:
        """该比特的寄存器参数（去掉 'q21_5.' 前缀）。"""
        pref = self.qubit + "."
        return {k[len(pref):]: v for k, v in self.params.items() if k.startswith(pref)}


def list_datasets(vault_dir, start=None, end=None) -> list[Dataset]:
    """列出 [start, end] 序号区间内的所有数据集。"""
    vault_dir = Path(vault_dir)
    out = []
    for p in sorted(vault_dir.glob("*.hdf5")):
        parsed = parse_name(p)
        if not parsed:
            continue
        idx, qubit, exp = parsed
        if start is not None and idx < start:
            continue
        if end is not None and idx > end:
            continue
        out.append(Dataset(index=idx, qubit=qubit, exp=exp, path=p))
    out.sort(key=lambda d: d.index)
    return out


def read_dataset(ds: Dataset) -> Dataset:
    """把 HDF5 读进 Dataset（列 + 参数）。"""
    with h5py.File(ds.path, "r") as f:
        grp = f["DataVault"]
        attrs = dict(grp.attrs)
        data = grp[()]
        names = data.dtype.names or ()

        # 列名：Independent0 先，其后 Dependent0..N
        cols = []
        for i in range(64):
            k = f"Independent{i}.label"
            if k in attrs:
                cols.append((i, str(attrs[k])))
        deps = []
        for i in range(64):
            k = f"Dependent{i}.label"
            if k in attrs:
                deps.append((i, str(attrs[k])))
        order = cols + deps
        for pos, (_, label) in enumerate(order):
            if pos < len(names):
                ds.columns[label] = np.asarray(data[names[pos]])

        ds.attrs = {k: v for k, v in attrs.items()
                    if k.startswith(("Independent", "Dependent"))}
        ds.creation_time = float(attrs.get("Creation Time", 0.0))

        # 参数：Param.<名字> 是 base64 tagged data
        for k, v in attrs.items():
            if not k.startswith("Param."):
                continue
            val = decode_labrad_value(_b64(v))
            if val is None:
                continue
            ds.params[k[len("Param."):]] = val
    return ds
