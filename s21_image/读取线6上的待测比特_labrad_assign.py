# -*- coding: utf-8 -*-
# 在 LabRAD 中粘贴并执行：把 S21 照片识别结果写入各比特 regs.fread
# 数据来源：02316 - qubit read_adc1: S21 find dips R6
# 读数文件：D:\QMClaw\s21_image\读取线6上的待测比特.csv

_fread_updates = {
    'q15_11': 6.2835,   # 单位 GHz
    'q13_13': 6.353,   # 单位 GHz
    'q17_9': 6.393,   # 单位 GHz
    'q11_15': 6.445,   # 单位 GHz
    'q19_7': 6.469,   # 单位 GHz
    'q9_17': 6.504,   # 单位 GHz
    'q5_21': 6.5465,   # 单位 GHz
    'q21_5': 6.556,   # 单位 GHz
    'q7_19': 6.6215,   # 单位 GHz
}

_ok, _fail = [], []
for _q, _v in _fread_updates.items():
    try:
        _obj = globals()[_q]
        _old = getattr(_obj.regs, 'fread', None)
        setattr(_obj.regs, 'fread', _v)
        _new = getattr(_obj.regs, 'fread', None)
        if _new is None or abs(float(_new) - _v) > 1e-6:
            _fail.append((_q, _v, '回读不一致: %s' % _new))
        else:
            _ok.append((_q, _old, _v))
    except Exception as _e:
        _fail.append((_q, _v, '%s: %s' % (type(_e).__name__, _e)))

print('=== 写入成功 %d 条 ===' % len(_ok))
for _q, _old, _v in _ok:
    print('  %-10s %s -> %s' % (_q, _old, _v))
if _fail:
    print('=== 失败 %d 条 ===' % len(_fail))
    for _q, _v, _err in _fail:
        print('  %-10s %s  (%s)' % (_q, _v, _err))
