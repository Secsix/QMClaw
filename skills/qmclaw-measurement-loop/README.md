# qmclaw-measurement-loop

**测控状态驱动的标定循环**——让智能体按状态机允许的边一步步推比特，而不是照死清单跑实验。

## 它解决什么

以前的「六阶段演示剧本」是写死的顺序：不管比特当前在什么状态，都按 iqraw -> piamp -> T1 跑一遍。
这里换成「每轮先问状态机允许哪些动作，再选一个」，于是：

- 不会在 `unknown` 上直接跑 T1（会被 G0 拒）；
- T1 需要的 `zpa`、spectroscopy 需要的 `freq` 这个脚本会停下来问人（G3），不瞎猜；
- 每一步的证据都从**本次 run 的指标**换算而来，不手填。

## 怎么用

```bash
py -3.12 server/tools/measurement_loop.py --qubit q0 --dry-run   # 先看状态和候选
py -3.12 server/tools/measurement_loop.py --qubit q0             # 跑
py -3.12 server/tools/measurement_loop.py --qubit q0 --decide-fn sq.piamp   # G3 后指定动作续跑
```

退出码：`0` 推进 / `2` G0 拒绝 / `3` 需要人挑动作 / `4` 闸门或授权拦截 / `5` 服务不通。
细节见 [`SKILL.md`](SKILL.md)。

## 前置

- `:3002` Express 网关、`:3003` quantum_service、`:3008` workflow_service 在跑
  （**由人启动**，本仓约定服务启停不入自动化）。
- 写闸门默认关闭；`sq.spectroscopy` 这类会写参数的实验需要人显式开授权。

## 相关

- 状态图与 scope：[`../qmclaw-state-graph/`](../qmclaw-state-graph/)
- 循环契约：[`../../docs/MEASUREMENT_LOOP.md`](../../docs/MEASUREMENT_LOOP.md)
