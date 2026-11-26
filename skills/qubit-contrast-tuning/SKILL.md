---
name: qubit-contrast-tuning
description: >
  态区分度（contrast）迭代调优：比特"能读出但分不清 |0> 和 |1>"时的诊断与收敛路径。
  核心判断——对比度卡住先怀疑频率而不是幅度；区分度判据看 contrast 不看 discriminator 中心；
  平坦光谱的拟合峰位不可信。含实测基线与五条现场坑。
  Triggers: '对比度', 'contrast', '区分度', '分不清 0 和 1', 'iqraw 不好', 'SNR 低',
  'discriminator', '读出不好', '态区分'
version: 2.0.0
author: qmclaw
---

# 态区分度（contrast）调优

现象：比特能读出信号（有 IQ 云团、有峰），但 |0> 与 |1> 分不开。
**不要**直接重跑扫频，也**不要**盲目调读出功率。按下面的顺序排。

## 一、先把 contrast 算清楚

判据是 `contrast`（`server/config/lab_criteria.json`）：

| 区间 | 结论 |
| --- | --- |
| `< 0.2` | 不可用：这样采下去所有结论都不可信 |
| `0.2 – 0.4` | 弱可用：能推进状态，但结论要标"低区分度" |
| `> 0.4` | 合格 |
| `> 0.7` | 良好（演示与教学都用这一档） |

取数方式：

```
POST http://localhost:3002/api/analysis/fit
{"dataset_id": "<iqraw 的数据集>", "kind": "iqraw"}
```

返回 `metrics.contrast`（若原始数据只有 separation 或 SNR，换算口径已在服务里：
`contrast = separation/2`，退化时 `contrast = SNR/20`，都截断到 0..1）。

**不要用两个 discriminator 中心之间的距离当区分度**：中心每次读都可能重算，
它变了不代表物理变了。判据只看 `contrast`。

## 二、迭代序列（哪一步先做）

1. **先确认频率**。频率错了，怎么调 π 幅度对比度都上不去。
   - 看 `freq_locked` 证据在不在、有没有过期（TTL 1 天）；
   - 过期或怀疑漂了：重扫（`spectroscopy`）；峰位不可信时用 `pidf` 做功率依赖修正；
   - 平坦光谱的拟合峰位是噪声——**图看起来是平的，就不要信它写回的频率**。
2. **再确认 π 幅度**。频率对了之后，`piamp` 只在 1~2% 范围内微调，很快收敛。
   如果 `piamp` 要动 >5%，说明上游（频率/脉冲形状）还有问题，别硬调。
3. **最后看读出**。读出声光子数不够、读出脉冲长度不当、态制备不干净，
   都会压低 contrast；这些是实验参数，不是拟合问题。
4. **每轮都重判状态**：`POST /api/quantum/qubit/state {"name":"q0"}`。
   对比度上去了，状态应从 `amp_calibrated` 推到 `state_discriminated`；
   没推进就如实说没推进。

## 三、典型症状 → 先查什么

| 症状 | 先查 | 不要做 |
| --- | --- | --- |
| contrast 长期 < 0.2，IQ 两团重叠 | 频率是否真的锁住（`pidf` 复核） | 只调读出功率 |
| contrast 在 0.3 附近徘徊，重跑也不变 | 态制备与读出脉冲长度 | 反复重跑 iqraw |
| 今天好明天差 | 状态是否过期、频率是否漂（TTL） | 假设"昨天已标定过" |
| IQ 云团呈一条线（不是两团） | 是否根本没加 π 脉冲 / 幅度为 0 | 拟合参数 |
| 拟合 r² 很低 | 数据本身有问题（点数太少、扫描范围不对） | 相信拟合结果 |

## 四、实测基线（供对照）

一台超导比特（q2ld3，2026-11 记录）在正常标定后的量级：

- `contrast ≈ 0.71`（separation ≈ 1.41，SNR ≈ 4）；
- `pi_amplitude ≈ 0.275`；
- `T1 ≈ 49.5 μs`、`T2* ≈ 20.7 μs`、`xeb_gate_fidelity ≈ 0.995`。

这些数字只用来判断"量级对不对"，不是判据。判据永远在配置文件里。

## 五、坑（都踩过）

1. **平坦光谱会写回一个假频率**：拟合在平数据上也能给出峰位，且看起来"像真的"。
   凡是光谱图没有明显峰，就不要拿它的 `f10`。
2. **对比度掉可能是频率先掉**：顺序永远是 频率 → 幅度 → 读出，倒过来调会白跑。
3. **过期的状态不能当依据**：`freq_locked` 的有效期只有 1 天；用它来做准入判定会
   被 GATE-TTL 拦下（这就是它存在的意义）。
4. **别用"跑完了"代替"推进了"**：一次 iqraw 跑完但没写进证据，状态不会动。
5. **模拟后端的 contrast 是算出来的**：qiskit 给的是 separation 与 SNR，
   换算出来的 contrast 恰好落在 0.7 左右；演示时说明这是模拟值。

## 六、调不上去怎么办

- 连续两轮同状态不推进 → 停，把 `missing` 与波形证据交人（别死循环）；
- 自环次数（`recenter` 等）有上限，用尽 Router 会判 hold；
- 需要改阈值 → 改 `server/config/lab_criteria.json`（由人改，智能体不改配置），
  并在 `.Codex/progress.md` 记一笔。

## 相关

- 全流程：`skills/qubit-calibration/SKILL.md`
- 分析口径：`skills/qubit-data-analysis/SKILL.md`
- 状态与闸门：`skills/qmclaw-state-graph/SKILL.md`
