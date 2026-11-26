---
name: qmclaw-state-graph
description: >-
  量子比特状态转移图：四张并列的 scope（单比特六态 / 双比特 CZ / 脉冲质量 / 频率空间）、
  边与闸门语义、声明式 when 状态表达式、按芯片能力启用哪些图、换芯片要改什么。
  触发词：状态转移图、状态机、scope、比特调到哪一步、还缺什么证据、换芯片、
  device_capabilities、state_machine.json、lab_criteria、when 表达式、闸门、G0 G4、
  状态过期、TTL、边缘边、自环、回退边、pulse、tunable、pair、CZ。
version: 1.0.0
author: QMClaw
---

# 量子比特状态转移图

状态图回答两个问题：**现在到哪一步**、**到下一步还缺什么**。缺的东西具体到「哪个证据键、由哪个实验产出」，
所以智能体不需要猜下一步该跑什么实验——查图就有。

## 一、四张并列的图（scope）

一张图管一件事，互不覆盖。同一个比特可以同时出现在四张图里，各自有自己的状态。

| scope | 状态数 | 管什么 | 典型启用条件 |
|-------|--------|--------|--------------|
| `qubit` | 6 | `unknown -> freq_locked -> amp_calibrated -> state_discriminated -> coherence_measured -> characterized` | 永远启用（结构表默认） |
| `pair` | 6 | 双比特 CZ：`pair_ready -> cz_point_found -> phase_locked -> frame_corrected -> leakage_bounded -> gate_verified` | 芯片有耦合器（`coupler`） |
| `pulse` | 3 | 脉冲质量：`pulse_unknown -> drag_tuned -> fidelity_verified` | 需要 DRAG 标定（`drag_required`） |
| `tunable` | 3 | 频率空间：`tunable_unknown -> zpa_mapped -> t1_mapped` | 比特频率可调（`tunable`） |

边分三类，前端用线型区分：`forward`（前向，实线）、`self`（自环，如重扫/重调，虚线）、
`back`（回退，如判据掉档，红虚线）。

**不要在一处硬编码这些图**。前端 `/control` 也是先读 `GET /qubit/scopes` 再决定画哪几张。

## 二、先查再动：三个端点

```bash
# 1) 有哪几张图、各自为什么启用
curl -s http://localhost:3002/api/quantum/qubit/scopes

# 2) 某张图的结构（状态 + 边 + 闸门 + 自环次数）
curl -s "http://localhost:3002/api/quantum/qubit/state-table?scope=pulse"

# 3) 某个比特在某张图上的当前状态、缺口、A(s) 合法转移
curl -s -X POST http://localhost:3002/api/quantum/qubit/state \
  -H "Content-Type: application/json" -d '{"name":"q0","scope":"pulse"}'
```

`POST /qubit/state` 返回里最该看的三样：

- `state.state`：当前状态名；`state.missing`：缺的裸证据键（脚本用）；
- `plan.missing`：`[{key, produced_by, note}]`——缺什么、由哪个实验产出、注意事项（给人看）；
- `edges`：`A(s)`，从当前态出发的合法转移（Router 只在这里面选动作）。

## 三、状态怎么算出来的：两套引擎

- **legacy**：`server/services/common/qubit_state.py` 里的 `derive_state()` 硬编码判据。
  这是**红线文件**（上游镜像在 `vendor/OpenQWF/qwf/qubit_state.py`），只在 `qubit` scope 用。
- **config**：图上的每个状态带一个声明式 `when`，由 `server/services/common/state_expr.py` 求值。
  `pair` / `pulse` / `tunable` 都用这套。

`when` 的写法（`server/config/state_machine.json` 里每个 state 的 `when` 字段）：

```json
{"flag": "freq_locked"}                                   // 布尔标记，真即满足
{"evidence": "contrast", "ok": true}                      // 判据通过（走 lab_criteria 的阈值）
{"evidence": "contrast", "verdict_not_in": ["unusable"]}  // 判据结论不在黑名单
{"evidence": "t1_us", "ge": 20}                           // 数值比较（ge/le/gt/lt）
{"metric": "xeb", "ok": true}                             // 按判据名（多键指标如 xeb 会自己拼）
{"prerequisite": {"scope": "qubit", "state": ">=state_discriminated"}}
```

组合用 `all_of` / `any_of` / `none_of`。状态按 `index` 取**最深的满足态**——所以判据放宽松会让状态一跳好几级。

## 四、启用哪些图：三层配置

| 层 | 文件 | 谁改 |
|----|------|------|
| base 结构 | `server/config/state_machine.json` | 开发者（改边、加状态） |
| chip 能力 | `server/config/device_capabilities.json` | 换芯片时由人改 |
| 判据阈值 | `server/config/lab_criteria.json` | 由人改（等于改判定标准） |
| runtime | `server/data/state_ledger/`、`server/data/run-records/` | 服务自己写，不要手改 |

启用判定顺序：**人工覆盖 `scopes.<name>.enabled` > 能力标签 `capabilities` > 结构表默认**。
没有 `device_capabilities.json` 时全部按结构表启用（老部署行为不变）。

换一台芯片通常只改 `device_capabilities.json`：

```json
{
  "chip": "LQHL-20251103_LD_SQ",
  "capabilities": { "tunable": true, "coupler": true, "drag_required": true },
  "scopes": { "tunable": { "enabled": false } },
  "edge_overrides": { "state_discriminated--self--drag": { "max_attempts": 5 } },
  "criteria_overrides": {}
}
```

`edge_overrides` 把某条边设成 `{"disabled": true}` 后，这条边从 `profile_machine()` 里消失，
Router 的 G0 就会自然拒绝该动作——**不需要在 Router 里加分支**。

## 五、三道闸门与 Router

每次 `POST /qubit/route` 会先过闸门，再剪枝，再选出口：

| 闸门 | 拦什么 |
|------|--------|
| `GATE-PRE` | 动作不在 `A(s)` 内 / 前置状态没到位 |
| `GATE-VER` | 记录或动作的 `criteria_version` 与当前判据不符 |
| `GATE-TTL` | 状态过期（每态有 TTL，`unknown` 永不过期，`characterized` 30 天） |

出口：`G0 reject`（非法边，必拒）→ `G1 accept`（模板命中且稳定）→ `G2 accept + 参数刷新` →
`G3 ask_llm`（模板库空时落到这里，把候选交给人/上层 Agent 挑）→ `G4 hold`（写授权关闭、自环用尽、连续失败）。

`pair` 的动作目前**一律 HOLD**：`lab_criteria.json` 的 `capabilities.two_qubit_vendor.dsl_support`
是 `false`，DSL 还没有耦合器表达。上游 `Secsix/OpenQWF` 支持后把它置 `true` 即自动放开。

## 六、红线

1. **不改 `server/services/common/qubit_state.py`**（红线 + 上游镜像）。
2. **不改 `vendor/OpenQWF/`**；DSL 语义改动只在上游仓做。
3. **判据阈值只能由人改**；改完 `lab_criteria.json` 的 `version` 会让旧记录在 `GATE-VER` 处失效，需要重跑。
4. **前端不许写死状态列表**；一切走 `/qubit/scopes` 与 `/state-table?scope=`。
5. 真机写闸门默认关闭，需要 `allow_execute` / `allow_set_params` 时由人改配置并重启。

## 七、相关

- 完整说明：`docs/STATE_GRAPH_PROFILES.md`（三层配置、换芯片流程、每条边的语义）
- 设计稿（私有）：`_local/docs/STATE_MACHINE_DESIGN.md`
- 驱动循环：`skills/qmclaw-measurement-loop/`
