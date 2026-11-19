"""厂商语义表（实体版，R4 迁仓后不再回退 services.common）。

来源：QMClaw 仓 `services/common/replay.py` 的 `VENDOR_SEMANTICS`（R4 物理搬过来，
新仓以本文件为唯一真源；QMClaw 侧的 `dsl/vendor_semantics.py` 保持惰性代理）。
"""

from typing import Any, Dict

VENDOR_SEMANTICS = {'spectroscopy': {'summary': '扫频找共振，auto-update 回写 f10/f21', 'auto_writes': ['f10', 'f21'], 'requires': ['freq'], 'defaults': {'freq': '缺省 → 退化为标量 → IndexError: list index out of range'}, 'note': '平坦数据上会回写一个错误的 f10（实测上午错 +35.7 MHz，pidf 后纠正 −35.55 MHz）'}, 'spectroscopy_auto': {'summary': 'z 脉冲 AC-Stark 自动谱学', 'auto_writes': ['f10', 'f21'], 'requires': [], 'defaults': {'zpa_scan': '默认 -3..3 step 0.4', 'freq_scan': '默认跟随 f10'}, 'note': "唯一可能看到'腔频随 zpa 弯曲'的实验"}, 'spectroscopy_iq': {'summary': 'IQ 域谱学（源码有、运行时被 .pyd 遮蔽 → 不可用）', 'auto_writes': ['f10', 'f21', 'fc'], 'requires': [], 'defaults': {}, 'note': '当前环境不可用：.pyd 遮蔽 .py，需实验室重新编译 lqms 扩展'}, 's21': {'summary': '腔反射谱，auto-update 回写 fread', 'auto_writes': ['fread'], 'requires': [], 'defaults': {'freq': '缺省 → fread 邻域自动扫描'}, 'note': '会把该比特自己的 fread 改成扫到的凹陷频率（曾把 q1/q3 的 fread 写成 6.7255 → 假死）'}, 'iqraw': {'summary': '裸 IQ 采集，auto-update 回写 discriminator 中心（有时连带 fread）', 'auto_writes': ['discriminator.center0', 'discriminator.center1', 'fread'], 'requires': [], 'defaults': {'states': '默认四个分支 (Is|Qs|Is|Qs)'}, 'note': "弱对比度数据上会写出'看起来分得很开'的假中心 → 判读要看 contrast 而非中心间距"}, 'pidf': {'summary': '扫 df 找比特跃迁，auto-update 回写 f10', 'auto_writes': ['f10'], 'requires': [], 'defaults': {'df': '默认 0.03（±30 MHz）'}, 'note': 'df 太小会扫空（实测偏差 35.7 MHz 时 0.03 覆盖不到，需 ≥0.05）'}, 'piamp': {'summary': '标定 π 脉冲幅度，回写 PiGate.amp（不动 alpha）', 'auto_writes': ['pigate.amp'], 'requires': [], 'defaults': {}, 'note': '只改幅度；若频率不对，幅度扫描不会改善对比度'}, 'set_pi': {'summary': '细调 π 脉冲，回写 PiGate.amp/alpha', 'auto_writes': ['pigate.amp', 'pigate.alpha'], 'requires': [], 'defaults': {'gate': "默认 'X'"}, 'note': 'tbuffer=0 时不可用于标定'}, 'ramsey_df': {'summary': 'Ramsey 测频偏，auto-update 回写 f10/f21', 'auto_writes': ['f10', 'f21'], 'requires': [], 'defaults': {'delay': '默认 Ramsey 延迟序列'}, 'note': '实测一次修正 +0.672 MHz'}, 'ramsey_t2': {'summary': 'Ramsey 测 T2*（不写寄存器）', 'auto_writes': [], 'requires': [], 'defaults': {}, 'note': ''}, 'spinecho_t2': {'summary': 'CPMG 测 T2（不写寄存器）', 'auto_writes': [], 'requires': [], 'defaults': {}, 'note': ''}, 't1': {'summary': '测 T1（不写寄存器）', 'auto_writes': [], 'requires': ['zpa'], 'defaults': {'zpa': '缺省 → zpa×时间二维扫描（极慢）；只测单个 zpa 必须显式传 zpa=<固定值>'}, 'note': "T1 拟合需要 zpa 维度，1D 数据 qter.fitData 会报 KeyError 'z pulse amplitude'"}, 'xeb': {'summary': 'XEB 随机基准保真度（不写寄存器）', 'auto_writes': [], 'requires': [], 'defaults': {'k': '默认 30', 'stats': '默认 300'}, 'note': '单次约 110 s'}}


# 会被仿真的函数别名（写错名字也要能发现）
KNOWN_SQ_FUNCTIONS = sorted(VENDOR_SEMANTICS.keys())


def get_vendor_semantics() -> Dict[str, Dict[str, Any]]:
    """返回厂商语义表（只读语义，调用方不应修改）。"""
    return VENDOR_SEMANTICS


def known_sq_functions():
    """已知的 sq.<fn> 名字列表。"""
    return list(KNOWN_SQ_FUNCTIONS)


def __getattr__(name: str):
    if name in ("VENDOR_SEMANTICS", "KNOWN_SQ_FUNCTIONS"):
        return globals()[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
