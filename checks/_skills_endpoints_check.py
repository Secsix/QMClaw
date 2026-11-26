"""端点技能新鲜度自检：`skills/qmclaw-service-endpoints/SKILL.md` 必须与路由表一致。

背景（2026-11-26）：这份技能是**生成物**——`server/tools/gen_service_endpoints.py`
从 Express 路由表 + Python 微服务 dispatch 里扫出端点和用途。手改它会在下次生成时
被覆盖，而**忘了重新生成**会让 Agent 照着一份过期清单去调不存在的路径。

本自检就是 `gen_service_endpoints.py --check` 的 CI 包装：内容是纯渲染对比，不连服务。
不一致时提示运行生成器，而不是让人去手改。

用法：
    python checks/_skills_endpoints_check.py
退出码：0 最新；1 过期。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

try:  # 控制台 GBK 时不要让自检打崩
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # pragma: no cover
    pass

ROOT = Path(__file__).resolve().parent.parent
GEN_PATH = ROOT / "server" / "tools" / "gen_service_endpoints.py"


def _load_generator():
    spec = importlib.util.spec_from_file_location("gen_service_endpoints", GEN_PATH)
    if spec is None or spec.loader is None:  # pragma: no cover - 文件缺失时
        raise RuntimeError(f"加载不了生成器：{GEN_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("gen_service_endpoints", module)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    if not GEN_PATH.is_file():
        print(f"[FAIL] 找不到生成器：{GEN_PATH}")
        return 1
    try:
        gen = _load_generator()
        rc = gen.main(["--check"])
    except Exception as exc:  # noqa: BLE001
        print(f"[FAIL] 自检异常：{exc}")
        return 1
    if rc == 0:
        print("[PASS] skills/qmclaw-service-endpoints 与路由表一致")
        return 0
    print("[FAIL] skills/qmclaw-service-endpoints 过期（请重新生成，不要手改）")
    return 1


if __name__ == "__main__":
    sys.exit(main())
