# 状态图随芯片变化（scope + 能力档案）

这份文档讲一件事：**同一条链路，状态图不是固定的。** 12 比特可调芯片要看「频率空间」，
只有固定比特的芯片就不该看到这两级；没有耦合器的芯片不该出现双比特 CZ。所以状态图由
**能力档案**推导，而不是写死在前端。

相关代码：`server/services/common/machine_profile.py`（唯一入口）、`server/services/common/state_expr.py`（声明式判据）
相关文档：`docs/MEASUREMENT_LOOP.md`（循环怎么用这张图）、`docs/CONTROL_CONSOLE_DEMO.md`（控制台怎么演示）

## 1. 三层配置

| 层 | 文件 | 管什么 | 谁改 |
|----|------|--------|------|
| base | `server/config/state_machine.json` | 结构真源：有哪些 scope、每个 scope 的状态/边/`when`/TTL | 人（改结构） |
| chip | `server/config/device_capabilities.json` | 这台机器：能力标签、scope 人工开关、单边覆盖 | 人（换芯片 / 特例） |
| runtime | `data/state_ledger/`、`data/run-records/` | 证据、模板实例、运行台账 | 程序 |

判据阈值在 `server/config/lab_criteria.json`（数值真源，与结构分开：边表是结构，阈值是数值）。

**没有能力档案时全部 scope 按结构表启用** —— 老部署、新克隆仓库的默认行为不变。

## 2. 一个 scope 是否启用

```
scope 有 enabled_by: ["tunable"]?
   ├─ 档案里 scopes.<scope>.enabled 有明确 true/false  → 以人工覆盖为准（source=override）
   ├─ 否则看 capabilities 是否覆盖 enabled_by         → 覆盖即启用（source=capabilities）
   └─ 没有 enabled_by（如 qubit）                      → 结构表默认启用（source=structure）
```

查询：

```bash
curl -s localhost:3002/api/quantum/qubit/scopes | python -m json.tool
```

返回每个 scope 的 `enabled / reason / source / enabled_by / missing / engine / prerequisite / states / edge_count`。
前端 `/control` 只画启用的那几张，并在卡片副标题里写「为什么启用」（例如 `按能力启用：tunable`）。

当前本机（`LQHL-20251103_LD_SQ`，12 比特可调、需 DRAG、DCZTuner 双比特）四张图全开：

| scope | 标签 | 启用依据 | 状态 | 边 |
|-------|------|----------|------|----|
| `qubit` | 单比特六态 | 结构表默认 | 6 | 15 |
| `pair` | 双比特 CZ | `coupler` | 6 | 13 |
| `pulse` | 脉冲质量 | `drag_required` | 3 | 3 |
| `tunable` | 频率空间 | `tunable` | 3 | 4 |

## 3. 四个 scope 各管什么

**`qubit`（单比特六态）** —— `unknown → freq_locked → amp_calibrated → state_discriminated → coherence_measured → characterized`。
`freq_locked → state_discriminated` 与 `freq_locked → amp_calibrated → state_discriminated` **两条并列**，
顺序谁先都合法，由 Router 按已有证据选（先有区分度就先做 iqraw，跳过幅度标定）。
另有 T2 自环 `sq.ramsey_df`、DRAG 自环 `sq.drag_calibrate`（在 `amp_calibrated` 与 `state_discriminated` 各一条）。

**`pulse`（脉冲质量）** —— `pulse_unknown → drag_tuned → fidelity_verified`。
DRAG 系数偏差、脉冲形状、单比特门保真度。前置：单比特到 `state_discriminated`。

**`tunable`（频率空间）** —— `tunable_unknown → zpa_mapped → t1_mapped`。
先用 `sq.spectroscopy_auto` 拿到 zpa↔f10 曲线（≥5 点），再在不同 zpa 下测 T1 得到「频谱地形」（≥5 点）。
只有频率可调的比特才有这两级。前置同上。

**`pair`（双比特 CZ）** —— 见 `_local/docs/STATE_MACHINE_DESIGN.md` §8；执行层仍显式 HOLD
（`lab_criteria.json` 的 `capabilities.two_qubit_vendor.dsl_support = false`）。

## 4. 状态怎么判：两套引擎

`scopes.<scope>.engine`：

- `legacy` —— 判据实现硬编码在 `server/services/common/qubit_state.py`（六态、pair 六态用它）。**红线文件，不改。**
- `config` —— 判据写在 `state_machine.json` 里每个状态的 `when`，由 `state_expr.py` 求值（`pulse` / `tunable` 用它）。

`when` 是一个小表达式，支持：

```json
{"all_of": [{"evidence": "t1_us", "ok": true}, {"evidence": "t2_star_us", "ok": true}]}
{"any_of":  [{"flag": "amp_calibrated"}, {"evidence": "contrast", "verdict_not_in": ["unusable"]}]}
{"none_of": [{"evidence": "cz_leakage", "verdict_in": ["too_leaky"]}]}
{"evidence": "cphase", "ge": 3.09, "le": 3.19}     // 也可用 verdict_in / ok / is / verdict_not_in / gt / lt
{"metric": "t1", "ok": true}                        // 直接点名判据
{"prerequisite": {"scope": "qubit", "state": ">=state_discriminated"}}
```

叶子里 `evidence` 会按 `metrics.<name>` 走 `services/common/metrics.py` 判定；未知指标**返回 False 而不是抛错**
（一个没实现的判据不该让整张图 500）。

**为什么 `qubit` 还没切到 `config`**：`state_expr` 与 `qubit_state.derive_state` 的等价性由
`server/tests/test_state_expr_parity.py` 守着（枚举 + 随机 ≥300 组证据逐条比对）。等这套 parity 长期稳定再切默认引擎；
切的时候只改一行 `"engine": "config"`，行为应当不变。

## 5. 换芯片 / 改边：`device_capabilities.json`

```json
{
  "chip": {"name": "LQHL-20251103_LD_SQ", "qubits": 12},
  "capabilities": ["tunable", "drag_required", "coupler", "readout"],
  "scopes": {"tunable": {"enabled": false}},              // 人工覆盖优先于能力推导
  "edge_overrides": {"qubit": {"state_discriminated--self--drag": {"action": {"fn": "sq.allxy"}}}},
  "criteria_overrides": {}                                 // 预留：按站点改阈值，优先级高于 lab_criteria
}
```

- **能力标签**决定「有没有这张图」：固定频率芯片去掉 `tunable`，`tunable` scope 自动消失，前端不画。
- **`scopes` 是人工覆盖**：芯片支持但暂时不想用，写 `{"enabled": false}`，理由会写成 `override`。
  反过来也能在缺能力时强开（用于调试），但要清楚边能不能真跑。
- **`edge_overrides` 裁边或改边**：按边 id 给补丁（`{"enabled": false}` 直接裁掉）。
  **被裁掉的边在 `/qubit/route` 里也不存在** —— Router 的 G0 会自然拒绝，不需要额外的"禁用表"。
- 改完不用重启服务：档案按 mtime 缓存，下次请求自动重读。

## 6. 端点

| 端点 | 用途 |
|------|------|
| `GET /api/quantum/qubit/scopes` | 有哪些图、为什么启用 |
| `GET /api/quantum/qubit/state-table?scope=pulse` | 某张图的态与边（不带 scope = 单比特） |
| `POST /api/quantum/qubit/state` `{"name":"q0","scope":"pulse"}` | 某张图上这个比特当前在哪 |
| `POST /api/quantum/qubit/route` `{"name":"q0"}` | 单比特路由（非 qubit scope 暂未开放路由） |

`scope` 未启用 → `error_envelope("scope_disabled")`（HTTP 400，`error_code` 是码，`error` 是中文说明）。
Express 代理会透传 query（`proxyToService` 的 `query` 参数），否则 `?scope=` 会被悄悄丢掉。

## 7. 红线

1. **`server/services/common/qubit_state.py` 不改**（上游 `vendor/OpenQWF/qwf/qubit_state.py` 有镜像）。
2. **`vendor/OpenQWF/` 不动**（DSL 语义只在上游改）。
3. **配置只由人改**：`state_machine.json` / `device_capabilities.json` / `lab_criteria.json` / `fallback_config.json`。
   脚本与智能体只读，不写。
4. 新判据的阈值必须标 `TODO: calibrate`，不要凭空给一个"看起来合理"的数。
