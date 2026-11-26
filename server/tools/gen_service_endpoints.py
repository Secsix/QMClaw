"""生成「服务端点」技能（``skills/qmclaw-service-endpoints/SKILL.md``）。

为什么自动生成
--------------
这个技能以前是手写的 430 行清单，于是它必然会过期：新增的 ``/qubit/scopes``、
``/qubit/route``、``/experiments/run``、``/fit`` 全部不在里面，读到旧清单的人会以为
「QMClaw 没有这些能力」。现在唯一真源是各微服务 ``handle_request`` 里的路由分发，
本脚本扫出来直接写进技能。

用法::

    py -3.12 server/tools/gen_service_endpoints.py            # 写文件
    py -3.12 server/tools/gen_service_endpoints.py --check     # 只比对，过期返回 1（CI 用）

扫描规则
--------
- ``services/<name>/server.py`` 里 ``handle_request`` 方法体内 ``path == "/xxx"`` /
  ``path in ("/a", "/b")`` 的字面量路径；
- 端口取 ``services/common/service_groups.py`` 的 ``SERVICE_PORTS``（唯一真源）；
- 网关前缀取 ``server/src/services/serviceProxy.ts`` 里出现的 ``/api/<section>``。
"""

from __future__ import annotations

import argparse
import ast
import pathlib
import re
import sys
from typing import Dict, List, Sequence, Tuple

SERVER_DIR = pathlib.Path(__file__).resolve().parent.parent
REPO_ROOT = SERVER_DIR.parent
SKILL_PATH = REPO_ROOT / "skills" / "qmclaw-service-endpoints" / "SKILL.md"
SERVICES_DIR = SERVER_DIR / "services"
PROXY_PATH = SERVER_DIR / "src" / "services" / "serviceProxy.ts"

#: 网关前缀 → 服务名（与 serviceProxy.ts 的分组一致；只用于说明，不参与扫描）
GATEWAY_SECTIONS: Dict[str, str] = {
    "quantum": "quantum",
    "analysis": "analysis",
    "agent": "agent",
    "workflow": "workflow",
    "hermes": "hermes",
    "qca": "qca",
    "image": "image",
    "llm": "llm",
    "tasks": "task_queue",
}

SECTION_TITLES: Dict[str, str] = {
    "quantum": "测控（量子实验执行、比特状态机、路由、证据）",
    "analysis": "分析（语义拟合、指标换算、离线复现）",
    "agent": "智能体会话",
    "workflow": "QWF 工作流",
    "hermes": "Hermes 技能与记忆",
    "qca": "QCA 变体生成",
    "image": "图像分类与分析",
    "llm": "模型调用",
    "tasks": "任务队列",
}

#: 语义层重点端点：单独列出来，说明"技能与智能体应该只调这些"
SEMANTIC_HIGHLIGHTS: Sequence[Tuple[str, str, str]] = (
    ("GET", "/api/quantum/experiments/catalog", "有哪些实验 kind、哪些后端支持、参数 schema"),
    ("POST", "/api/quantum/experiments/run", "按 kind 跑实验（lqcs / qiskit / offline_sim 三后端统一）"),
    ("GET", "/api/quantum/qubit/scopes", "当前芯片启用了哪几张状态转移图"),
    ("POST", "/api/quantum/qubit/state", "某比特/某个 scope 的当前状态、缺什么证据"),
    ("POST", "/api/quantum/qubit/route", "状态机路由判定（G0–G4，纯计算不执行）"),
    ("GET", "/api/quantum/qubit/templates", "模板库（G1/G2 的唯一开关）"),
    ("POST", "/api/analysis/fit", "按 kind 拟合数据集，产出物理量与证据"),
    ("GET", "/api/analysis/fit/kinds", "能拟合哪些 kind"),
    ("POST", "/api/analysis/metrics/convert", "运行指标 → 状态机证据键的唯一换算"),
    ("POST", "/api/analysis/replay/plot", "离线复现：按 dataset_id 重新拟合并出图"),
)


def service_ports() -> Dict[str, int]:
    """读端口表（``services/common/service_groups.py`` 是唯一真源）。"""
    path = SERVICES_DIR / "common" / "service_groups.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign):
            target, value = node.target, node.value
        elif isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
        else:
            continue
        if not (isinstance(target, ast.Name) and target.id == "SERVICE_PORTS"):
            continue
        if not isinstance(value, ast.Dict):
            continue
        ports: Dict[str, int] = {}
        for key, val in zip(value.keys, value.values):
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                if isinstance(val, ast.Constant) and isinstance(val.value, int):
                    ports[key.value] = val.value
        return ports
    raise SystemExit(f"{path} 里没有找到 SERVICE_PORTS")


def _handler_body(tree: ast.AST) -> ast.AST | None:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "handle_request":
            return node
    return None


def scan_service(path: pathlib.Path) -> List[str]:
    """从一个服务的 server.py 里扫出所有字面量路径（按出现顺序去重）。"""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError as exc:  # 扫不动就明确报错，别静默漏端点
        raise SystemExit(f"无法解析 {path}：{exc}") from exc
    body = _handler_body(tree)
    if body is None:
        return []
    found: List[str] = []
    for node in ast.walk(body):
        if isinstance(node, ast.Compare) and len(node.comparators) == 1:
            left, right = node.left, node.comparators[0]
            if isinstance(left, ast.Name) and left.id == "path":
                values: List[ast.AST] = []
                if isinstance(right, ast.Constant):
                    values = [right]
                elif isinstance(right, (ast.Tuple, ast.List)):
                    values = list(right.elts)
                for value in values:
                    if isinstance(value, ast.Constant) and isinstance(value.value, str):
                        if value.value.startswith("/") and value.value not in found:
                            found.append(value.value)
    return found


def gateway_prefix(section: str) -> str:
    return f"/api/{section}"


def collect() -> Dict[str, List[str]]:
    ports = service_ports()
    result: Dict[str, List[str]] = {}
    for name in sorted(ports):
        server_py = SERVICES_DIR / f"{name}_service" / "server.py"
        if not server_py.exists():
            continue
        paths = scan_service(server_py)
        if paths:
            result[name] = paths
    return result


def render(services: Dict[str, List[str]]) -> str:
    ports = service_ports()
    lines: List[str] = []
    lines.append("---")
    lines.append("name: qmclaw-service-endpoints")
    lines.append("description: >")
    lines.append("  QMClaw 微服务端点清单（自动生成，勿手改）。查某个能力由哪个服务提供、走哪个网关路径、")
    lines.append("  参数是什么。优先用语义端点（/experiments/*、/qubit/*、/fit），不要拼厂商代码。")
    lines.append("  Triggers: '接口', 'endpoint', 'API', '哪个服务', '怎么调', '网关', '端口', 'service endpoints'")
    lines.append("version: 2.0.0")
    lines.append("author: qmclaw")
    lines.append("---")
    lines.append("")
    lines.append("# QMClaw 服务端点")
    lines.append("")
    lines.append("> **本文件由 `server/tools/gen_service_endpoints.py` 生成**；改端点请改代码后重新生成，")
    lines.append("> `--check` 在 CI 里拦住过期清单。")
    lines.append("")
    lines.append("## 先看这张表：能力 → 该调哪个端点")
    lines.append("")
    lines.append("技能、智能体、脚本**只应该调这些语义端点**。它们内部才碰厂商库：")
    lines.append("")
    lines.append("| 想做什么 | 端点 | 说明 |")
    lines.append("| --- | --- | --- |")
    for method, path, note in SEMANTIC_HIGHLIGHTS:
        lines.append(f"| {note} | `{method} {path}` | |")
    lines.append("")
    lines.append("其余端点（`/execute`、`/plot/offline/v2`、自由命令字符串…）是**排障入口**，")
    lines.append("不是给技能用的：它们要求调用方会写厂商代码，换一台机器就失效。")
    lines.append("")
    lines.append("## 服务与端口")
    lines.append("")
    lines.append("| 服务 | 端口 | 网关前缀 | 端点数量 |")
    lines.append("| --- | --- | --- | --- |")
    for name in sorted(services):
        port = ports.get(name, "?")
        prefix = gateway_prefix(name) if name in GATEWAY_SECTIONS else "（不对外）"
        lines.append(f"| {name} | {port} | `{prefix}` | {len(services[name])} |")
    lines.append("")
    lines.append("`server/src/index.ts` 把 `:3002` 网关的 `/{section}/*` 转发到上表的服务端口；")
    lines.append("技能里写 `http://localhost:3002/api/<section>/<path>`，不要直连微服务端口。")
    lines.append("")
    for name in sorted(services):
        prefix = gateway_prefix(name)
        title = SECTION_TITLES.get(name, name)
        lines.append(f"## {name} · {title}")
        lines.append("")
        lines.append(f"网关前缀 `{prefix}`（直连 `:{ports.get(name, '?')}`）：")
        lines.append("")
        for path in services[name]:
            lines.append(f"- `{path}`")
        lines.append("")
    lines.append("## 约定")
    lines.append("")
    lines.append("1. **只调语义端点**：`/experiments/run` 收 `kind`，`/fit` 收 `kind`；两者各自知道")
    lines.append("   「这个实验在 qiskit 上做不了」和「这个 kind 该用什么模型」。")
    lines.append("2. **结果要留凭据**：实验返回 `run_id` / `dataset_ids`，把它们带上再谈结论。")
    lines.append("3. **证据只从运行来**：`/qubit/state` 的证据来自台账与演练库；不要把记忆里的数字直接写进去。")
    lines.append("4. **写闸门**：真机写操作默认关闭（`fallback_config.json` 的 actuation），")
    lines.append("   模拟后端（qiskit / offline_sim）不受影响。")
    lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成 qmclaw-service-endpoints 技能")
    parser.add_argument("--check", action="store_true", help="只检查是否最新（过期返回 1）")
    parser.add_argument("--out", default=str(SKILL_PATH), help="输出路径")
    args = parser.parse_args(argv)

    content = render(collect())
    out = pathlib.Path(args.out)
    if args.check:
        current = out.read_text(encoding="utf-8") if out.exists() else ""
        if current != content:
            print(f"过期：{out}（运行 py -3.12 server/tools/gen_service_endpoints.py 更新）")
            return 1
        print(f"最新：{out}")
        return 0
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(content, encoding="utf-8", newline="\n")
    print(f"已写入 {out}（{len(content.splitlines())} 行）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
