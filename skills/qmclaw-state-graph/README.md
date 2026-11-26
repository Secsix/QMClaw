# qmclaw-state-graph

**状态转移图**：告诉人和智能体「这个比特调到哪一步了、到下一步还缺什么、能做哪些实验」。

## 为什么要有它

标定不是一条固定流水线：有的比特要补幅度，有的要先做态区分，可调频的比特还要先扫 zpa-频率曲线。
状态图把这件事变成四张并列的小图，每张图有自己的状态集和边，缺的证据具体到「由哪个实验产出」。
于是「下一步跑什么」不再靠记忆和口口相传，而是查图就有答案。

## 四张图

| scope | 管什么 | 状态 |
|-------|--------|------|
| `qubit` | 单比特标定主链 | unknown → freq_locked → amp_calibrated → state_discriminated → coherence_measured → characterized |
| `pair` | 双比特 CZ | pair_ready → cz_point_found → phase_locked → frame_corrected → leakage_bounded → gate_verified |
| `pulse` | 脉冲质量（DRAG / 单比特保真度） | pulse_unknown → drag_tuned → fidelity_verified |
| `tunable` | 频率空间（zpa 曲线 / 不同 zpa 下的 T1） | tunable_unknown → zpa_mapped → t1_mapped |

哪几张图启用，由芯片能力档案决定（`server/config/device_capabilities.json`），换芯片只改这一份。

## 怎么用

```bash
curl -s http://localhost:3002/api/quantum/qubit/scopes                      # 有哪几张图
curl -s "http://localhost:3002/api/quantum/qubit/state-table?scope=pulse"   # 图结构
curl -s -X POST http://localhost:3002/api/quantum/qubit/state \
  -H "Content-Type: application/json" -d '{"name":"q0","scope":"pulse"}'    # 当前态 + 缺口
```

浏览器看：`http://localhost:8081/control` 的「测控状态转移」卡片，右上角切 scope。

## 相关

- 细节与换芯片流程：[`SKILL.md`](SKILL.md)、[`../../docs/STATE_GRAPH_PROFILES.md`](../../docs/STATE_GRAPH_PROFILES.md)
- 按图驱动标定：[`../qmclaw-measurement-loop/`](../qmclaw-measurement-loop/)
