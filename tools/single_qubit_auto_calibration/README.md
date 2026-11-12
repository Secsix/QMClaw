# single_qubit_auto_calibration

离线优先的自动化单比特标定策略实现。

## 功能

- S21 局部候选读取腔频率
- ZPA 网格扫描入口
- spectroscopy / IQraw / PiAmp / S21_dis / Ramsey / T1 结果指标化
- 读取优化、f10/PiAmp 优化、PIDF/Ramsey、ZPA/T1、最终验收策略
- 状态机、失败原因、候选工作点、断点 JSON
- Mock 适配器和 LabRAD/sq 显式安全适配层
- `assignment_plan.json` 与 `assignment_plan.py` 仅生成计划，默认不写硬件

## 运行离线 Mock 测试

```powershell
$py = 'C:\Users\lqcs\Programs\Python\Python311\python.exe'
$env:PYTHONPATH = 'D:\QMClaw'
& $py -m unittest discover -s D:\QMClaw\tools\single_qubit_auto_calibration\tests -p 'test_*.py' -v
```

## 生成计划

```powershell
$env:PYTHONPATH = 'D:\QMClaw'
& $py -m tools.single_qubit_auto_calibration.cli plan --qubit q21_5 --fread-ref 6.4 --out calibration_plan
```

真实执行和参数写入需要显式打开配置闸门；本模块默认 `dry_run=True`、`allow_execute=False`、`allow_set_params=False`。
