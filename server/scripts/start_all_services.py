"""
scripts/start_all_services.py - 启动所有服务

Usage:
    python start_all_services.py [service1, service2, ...]
    python start_all_services.py --all  # 启动所有服务
    python start_all_services.py llm quantum  # 只启动指定服务

    # 只跑 Hermes 技能自举、不启动任何服务（验证用）：
    QMCLAW_BOOTSTRAP_ONLY=1 python start_all_services.py --all

启动任何服务之前，会先幂等执行同目录下的 setup_hermes_skills.py
（换机后跑一次启动即完成 Hermes 技能接线）；自举失败只告警，不阻断服务启动。
"""

import subprocess
import sys
import os
import re
import signal
import time
import json
import threading
import queue
from pathlib import Path

# Windows 控制台/管道默认可能是 GBK。本脚本会打印 ✅/⏳/⚠️ 等字符，
# 一旦 stdout 编码不是 UTF-8，print 会抛 UnicodeEncodeError，
# 进而让整个启动流程异常退出并连带杀掉已经起来的子服务。
# 这里显式把两个流切到 UTF-8，避免"服务其实起得来、却被收尾逻辑干掉"。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# 加载 .env 文件
try:
    from dotenv import load_dotenv
    env_path = Path(__file__).parent.parent / ".env"

    # 只打印「是否已设置」，绝不打印密钥值（密钥不进日志/终端/报告）
    def _key_state(name: str) -> str:
        return "(set)" if os.environ.get(name) else "(not set)"

    print(f"[start_all] Before load_dotenv:")
    print(f"[start_all]   MINIMAX_API_KEY = {_key_state('MINIMAX_API_KEY')}")

    if env_path.exists():
        load_dotenv(env_path, override=True)  # 添加 override=True 确保覆盖
        print(f"[start_all] Loaded .env from {env_path}")
        print(f"[start_all] After load_dotenv:")
        print(f"[start_all]   MINIMAX_API_KEY = {_key_state('MINIMAX_API_KEY')}")
    else:
        print(f"[start_all] .env file not found at {env_path}")
except ImportError:
    print("[start_all] python-dotenv not installed, .env file will not be loaded")

# 服务配置
SERVICES = {
    "llm": {
        "port": 3006,
        "script": "services/llm_service/server.py",
        "description": "LLM 推理服务",
    },
    "quantum": {
        "port": 3003,
        "script": "services/quantum_service/server.py",
        "description": "测控执行服务",
    },
    "analysis": {
        "port": 3004,
        "script": "services/analysis_service/server.py",
        "description": "数据分析服务",
    },
    "agent": {
        "port": 3005,
        "script": "services/agent_service/server.py",
        "description": "Agent 服务",
    },
    "image": {
        "port": 3007,
        "script": "services/image_service/server.py",
        "description": "图像服务",
    },
    "workflow": {
        "port": 3008,
        "script": "services/workflow_service/server.py",
        "description": "工作流服务",
    },
    "task_queue": {
        "port": 3009,
        "script": "services/task_queue/server.py",
        "description": "任务队列服务",
    },
    "qubitclient": {
        "port": 3010,
        "script": "services/qubitclient_service/server.py",
        "description": "QubitClient VLM 分析服务",
    },
    "qca": {
        "port": 3011,
        "script": "services/qca_service/server.py",
        "description": "QCA 量子校准智能体服务",
    },
    "hermes": {
        "port": 3012,
        "script": "services/hermes_service/server.py",
        "description": "Hermes Agent 服务",
    },
}

# 全局进程列表
processes = []
process_info = {}  # 存储进程详细信息
to_start = []      # 要启动的服务列表


def load_config():
    """从配置文件加载服务列表"""
    config_path = Path(__file__).parent.parent / "config" / "services.json"
    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            return json.load(f).get("services", {})
    return {}


# ── 子进程输出的三级分类 ────────────────────────────────────────────────────
# 「这行走的是 stderr」**不等于**「这里出错了」。
#
# 这个代码库**故意把服务日志写到 stderr**（services/base/base_service.py 的
# _log_writer 注释写得很清楚："日志消费线程：唯一允许写 stderr 的地方"），
# 而 Python 的 warning 也一律走 stderr。于是量子服务启动时的
# "[quantum_service] Initializing ..." / "Mode: auto" / "Started on localhost:3003"
# 这些**正常运行日志**全在 stderr 上。
#
# 原来这里对 stderr 的每一行都贴 [ERR]，结果满屏 [ERR]、一条真错误都没有，
# 真正出问题时反而被淹没。改成三级：
#   error —— 崩溃级输出（traceback / 未捕获异常 / ERROR·CRITICAL 级日志）
#   warn  —— warning 类输出（DeprecationWarning 等）
#   log   —— 其余（绝大多数是服务自己的正常运行日志）

_ERROR_PATTERNS = (
    "Traceback (most recent call last)",
    '  File "',                    # traceback 的栈帧行
    "Exception:",                  # 未捕获异常的末行
    "Error:",                      # AttributeError: / ModuleNotFoundError: ...
    "Fatal Python error",
    "Fatal error",
    "[ERROR]",
    "[CRITICAL]",
    "[FATAL]",
    "ERROR:",                      # logging 的 ERROR 级别
    "CRITICAL:",
)

#: 服务**自己**的结构化日志行（`[quantum_service] ...`）里，只有显式的错误级别
#: 才算错误。这类行经常把异常名写进说明文字里（"连不上（TimeoutError: timed out）"），
#: 那是**描述**而不是崩溃 —— 所以结构化行不能用内容里的 "Error:" 来判。
_LOGGED_ERROR_PATTERNS = (
    "[ERROR]",
    "[CRITICAL]",
    "[FATAL]",
    "ERROR:",
    "CRITICAL:",
    "FATAL:",
    "Traceback (most recent call last)",
)

_WARN_PATTERNS = (
    "Warning:",
    "DeprecationWarning",
    "FutureWarning",
    "PendingDeprecationWarning",
    "ResourceWarning",
    "RuntimeWarning",
)

#: 结构化日志行的前缀，例如 `[quantum_service] `、`[Config] `、`[LANE] `
_TAG_RE = re.compile(r"^\[[A-Za-z0-9_.\-]+\]\s*")


def classify_output_line(line: str) -> str:
    """把一行子进程输出归类为 'error' / 'warn' / 'log'。"""
    if _TAG_RE.match(line):
        # 服务自己的日志行：只看显式的错误级别
        if any(p in line for p in _LOGGED_ERROR_PATTERNS):
            return "error"
        if any(p in line for p in _WARN_PATTERNS):
            return "warn"
        return "log"
    # 原始 Python 输出（traceback / 未捕获异常）
    if any(p in line for p in _ERROR_PATTERNS):
        return "error"
    if any(p in line for p in _WARN_PATTERNS):
        return "warn"
    return "log"


def format_output_line(msg_name: str, is_error: bool, line: str, prefix_chars: int = 14) -> str:
    """把一行子进程输出渲染成终端上的一行。

    只有崩溃级输出带 [ERR]，warning 带 [WARN]，其余原样打印（不加标签）。
    """
    prefix = f"[{msg_name[:prefix_chars]:<{prefix_chars}}]"
    kind = classify_output_line(line) if is_error else "log"
    if kind == "error":
        return f"{prefix} [ERR] {line}"
    if kind == "warn":
        return f"{prefix} [WARN] {line}"
    return f"{prefix} {line}"


def read_output(name: str, pipe, output_queue: queue.Queue, is_error: bool = False):
    """读取子进程输出的线程函数"""
    try:
        for line in iter(pipe.readline, ""):
            if line:
                output_queue.put((name, is_error, line.rstrip("\n\r")))
    except (ValueError, OSError):
        pass  # 进程退出时管道被关闭，属正常收尾
    except Exception as exc:
        # 以前这里是 `except Exception: pass` —— 一旦解码出错（编码不一致），
        # **整个 stderr 流会被静默吞掉**，日志从那行起就没了，而且毫无提示。
        # 所以这里必须喊一声：否则"日志没了"会变成查不出原因的问题。
        print(f"[start_all] ⚠️  {name} 输出读取异常：{exc!r}（该服务后续日志会缺失）")


def print_output_loop(name: str, output_queue: queue.Queue):
    """定期从队列中取出输出并打印"""
    prefix = f"[{name:12}]"
    while True:
        try:
            msg_name, is_error, line = output_queue.get(timeout=0.1)
            if msg_name == name:  # 只打印本服务的消息
                if is_error:
                    print(f"{prefix} [STDERR] {line}")
                else:
                    print(f"{prefix} {line}")
        except queue.Empty:
            continue


def is_port_in_use(port: int) -> bool:
    """探测端口是否已被占用。

    端口被占通常意味着上一次运行留下了孤儿进程：新实例会绑定失败并以
    ``[Errno 10048]`` 退出，而调用方只会看到一句语焉不详的 "exited with code 1"。
    提前探测可以把这种问题在启动瞬间说清楚。
    """
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        # connect_ex() 返回 0 表示已有进程在监听
        return sock.connect_ex(("127.0.0.1", port)) == 0


def start_service(name: str, config: dict, output_queue: queue.Queue) -> subprocess.Popen:
    """启动单个服务"""
    script_path = config["script"]  # 如 "services/llm_service/server.py"

    # 转换为模块路径: services.llm_service.server
    module_path = script_path.replace("/", ".").replace("\\", ".").replace(".py", "")

    script_full_path = Path(__file__).parent.parent / script_path
    if not script_full_path.exists():
        print(f"[start_all] {name}: Script not found: {script_full_path}")
        return None

    # 启动前预检端口：被占时直接跳过，而不是让新进程崩溃后刷屏
    port = config.get("port", 3000)
    if is_port_in_use(port):
        print(f"[start_all] ⚠️  {name}: port {port} is already in use - skipping start")
        print(f"[start_all]    Likely a leftover process from an earlier run.")
        print(f"[start_all]    Inspect it with: netstat -ano | findstr :{port}")
        return None

    # 设置环境变量
    env = os.environ.copy()

    # 设置服务端口环境变量
    env["PORT"] = str(config.get("port", 3000))

    # 所有微服务都跑在后台、没有可交互的显示环境：统一禁用 matplotlib 的 GUI 后端。
    # 否则实验函数里的 do_plot=True 会在工作线程拉起 Qt 窗口，把服务卡死。
    env.setdefault("MPLBACKEND", "Agg")

    # **子进程一律用 UTF-8 写 stdout/stderr，父进程也一律按 UTF-8 读**。
    # 不锁死编码会踩一个很隐蔽的坑：Windows 中文环境下父进程按 locale(cp936/GBK)
    # 解码，而子进程若用 UTF-8（PYTHONIOENCODING / PYTHONUTF8 / 读 UTF-8 文件后
    # 再打印）就会触发 UnicodeDecodeError —— read_output 里那个 `except: pass`
    # 会把**整个 stderr 流静默吞掉**，于是日志从第一行中文开始就没了。
    # 服务日志里恰恰全是中文（"[backend] lqcs 不可用：…"），所以必须锁死。
    env["PYTHONIOENCODING"] = "utf-8"

    # 调试：打印相关环境变量
    if name == "llm":
        print(f"[start_all]   MINIMAX_API_KEY: {'***' if env.get('MINIMAX_API_KEY') else '(not set)'}")
        print(f"[start_all]   DEEPSEEK_API_KEY: {'***' if env.get('DEEPSEEK_API_KEY') else '(not set)'}")
        print(f"[start_all]   OPENAI_API_KEY: {'***' if env.get('OPENAI_API_KEY') else '(not set)'}")
    if name == "hermes":
        print(f"[start_all]   HERMES_HOME: {env.get('HERMES_HOME', '(not set)')}")
        print(f"[start_all]   MINIMAX_API_KEY: {'***' if env.get('MINIMAX_API_KEY') else '(not set)'}")

    print(f"[start_all] Starting {name} ({config['description']})...")
    print(f"[start_all]   Module: {module_path}")
    print(f"[start_all]   Port: {config['port']}")

    # 使用模块方式启动 (python -m services.llm_service.server)
    proc = subprocess.Popen(
        [sys.executable, "-m", module_path],
        cwd=Path(__file__).parent.parent,  # 设置工作目录为 server 目录
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,  # 使用文本模式
        encoding="utf-8",   # 与子进程的 PYTHONIOENCODING=utf-8 对齐
        errors="replace",   # 兜底：任何非法字节都不许把整条日志流打没
        bufsize=1,  # 行缓冲
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0) if sys.platform == 'win32' else 0,
    )

    # 启动输出读取线程
    stdout_thread = threading.Thread(target=read_output, args=(name, proc.stdout, output_queue, False), daemon=True)
    stderr_thread = threading.Thread(target=read_output, args=(name, proc.stderr, output_queue, True), daemon=True)
    stdout_thread.start()
    stderr_thread.start()

    # 存储进程信息
    process_info[name] = {
        "proc": proc,
        "config": config,
        "start_time": time.time(),
        "stdout_thread": stdout_thread,
        "stderr_thread": stderr_thread,
    }

    print(f"[start_all] {name}: Started (PID: {proc.pid})")
    return proc


def stop_all():
    """停止所有服务"""
    print("\n[start_all] Stopping all services...")

    for name, info in process_info.items():
        proc = info["proc"]
        if proc and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=5)
                print(f"[start_all] {name} (PID: {proc.pid}) terminated")
            except subprocess.TimeoutExpired:
                proc.kill()
                print(f"[start_all] {name} (PID: {proc.pid}) killed")

    # 等待一下让输出线程退出
    time.sleep(0.5)


def signal_handler(signum, frame):
    """处理信号"""
    print(f"\n[start_all] Received signal {signum}")
    stop_all()
    sys.exit(0)


def wait_for_services(timeout: int = 60):
    """等待所有服务就绪"""
    import urllib.request
    import urllib.error
    import json

    print(f"\n[start_all] Waiting for services to be ready (timeout={timeout}s)...")

    start_time = time.time()
    config = load_config()

    # 跟踪每个服务的状态
    service_status = {name: "starting" for name in to_start}
    # 跟踪是否已经打印过错误信息
    error_printed = {name: False for name in to_start}

    # Express 网关健康检查 URL
    express_url = "http://localhost:3002/health"

    while time.time() - start_time < timeout:
        all_ready = True

        # 先检查 Express 网关是否可用
        express_healthy = False
        try:
            req = urllib.request.Request(express_url)
            with urllib.request.urlopen(req, timeout=2) as response:
                if response.status == 200:
                    express_healthy = True
        except:
            pass

        for name in to_start:
            svc_config = config.get(name, SERVICES.get(name, {}))
            port = svc_config.get("port", SERVICES[name]["port"] if name in SERVICES else 0)
            url = f"http://localhost:{port}/health"

            status_code = None
            transport_error = None
            try:
                req = urllib.request.Request(url)
                with urllib.request.urlopen(req, timeout=2) as response:
                    status_code = response.status
            except urllib.error.HTTPError as e:
                # **503 不是"连不上"**。
                # urlopen 对非 2xx 响应会抛 HTTPError，而 HTTPError 是 URLError 的
                # 子类 —— 于是以前 503 落进了下面的 URLError 分支，被判成 "waiting"。
                # 量子/分析/比特这几个真机链路服务在没有设备时 /health 正常就返回
                # 503（降级但活着），结果脚本一直等到 60s 超时，报"服务没启动"，
                # 而服务其实好好地跑着。
                # 所以必须单独接住 HTTPError，拿它的 code 走同一套"就绪"判断。
                status_code = e.code
            except urllib.error.URLError as e:
                transport_error = str(e.reason)
            except Exception as e:
                transport_error = f"{type(e).__name__}: {e}"

            if status_code is not None:
                # 接受 200 (healthy) 或 503 (degraded/running) 作为"就绪"
                if status_code in (200, 503):
                    if service_status[name] != "ready":
                        # 根据状态码显示不同信息
                        if status_code == 200:
                            print(f"[start_all] ✅ {name}: Ready (port {port})")
                        else:
                            print(f"[start_all] ⚠️  {name}: Running in degraded mode (port {port})")
                        service_status[name] = "ready"
                else:
                    all_ready = False
                    if not error_printed[name]:
                        print(f"[start_all] ⏳ {name}: HTTP {status_code} (port {port})")
                        error_printed[name] = True
            else:
                all_ready = False
                reason = transport_error or ""
                # 检测是否是熔断器导致的错误（HTTP reason phrase 大小写不固定）
                if "service unavailable" in reason.lower() or "circuit open" in reason.lower():
                    if not error_printed[name]:
                        print(f"[start_all] ⏳ {name}: Circuit breaker open (Express gateway cannot reach service)")
                        error_printed[name] = True
                    service_status[name] = "circuit_open"
                else:
                    if not error_printed[name]:
                        print(f"[start_all] ⏳ {name}: Waiting... ({reason})")
                        error_printed[name] = True
                    service_status[name] = "waiting"

        # 检查是否可以认为服务已就绪
        # 如果所有服务要么已就绪，要么被熔断器阻止（服务本身在运行），则认为成功
        non_waiting = [name for name, status in service_status.items()
                       if status in ("ready", "circuit_open")]
        # 全部服务都已就绪（或仅被熔断器标记，服务本身在跑）即视为成功。
        # 旧条件还要求 any_running 为真，而任何服务返回 200/503 时它都不会被置位，
        # 于是健康启动永远走不到这里 —— 必然跑满 60s 超时并误报失败。
        if len(non_waiting) == len(to_start):
            elapsed = time.time() - start_time
            circuit_open = [name for name, status in service_status.items() if status == "circuit_open"]
            if circuit_open:
                print(f"[start_all] Services ready (circuit breakers will auto-recover in ~30s): {', '.join(circuit_open)}")
            print(f"[start_all] All services started! (took {elapsed:.1f}s)")
            return True

        # 如果 Express 网关不可用，给出提示
        if not express_healthy:
            print(f"[start_all] ⚠️  Express gateway (port 3002) not healthy - services may report false failures")

        time.sleep(1)

    # 超时时显示哪些服务未就绪，并给出诊断建议
    print("\n[start_all] ⚠️  Timeout waiting for services")
    print("[start_all] Service status:")
    for name, status in service_status.items():
        status_icon = "✅" if status == "ready" else "⏳" if status == "waiting" else "🔌"
        print(f"  {status_icon} {name}: {status}")

    # 给出诊断建议
    circuit_open_services = [name for name, status in service_status.items() if status == "circuit_open"]
    if circuit_open_services:
        print(f"\n[start_all] 💡 Tip: Circuit breaker is open for: {', '.join(circuit_open_services)}")
        print("[start_all]    This usually means Express gateway had previous failures.")
        print("[start_all]    The circuit will auto-recover in 30 seconds, or restart Express gateway.")

    return False


def run_hermes_skills_bootstrap():
    """启动服务前自举 Hermes 技能接线（幂等；失败不阻断）。

    用同一个解释器（sys.executable）运行同目录下的 setup_hermes_skills.py：
    确保 $HERMES_HOME/config.yaml 的 skills.external_dirs 含 <repo>/skills，
    并确保 <server>/data/skills 是指向 <repo>/skills 的目录联接。
    子脚本的 stdout/stderr 逐行加 [bootstrap] 前缀转发，便于终端确认结果。

    任何异常 / 非 0 退出 / 超时都只打印 [bootstrap] WARNING ...，然后继续。

    Returns:
        bool: True 表示 QMCLAW_BOOTSTRAP_ONLY 为真值，调用方应跳过服务启动。
    """
    print("[bootstrap] Running Hermes skills bootstrap (setup_hermes_skills.py) ...")
    script = Path(__file__).with_name("setup_hermes_skills.py")

    try:
        completed = subprocess.run(
            [sys.executable, str(script)],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        print("[bootstrap] WARNING: setup_hermes_skills.py timed out after 60s (continuing)")
    except Exception as exc:
        print(f"[bootstrap] WARNING: could not run setup_hermes_skills.py: {exc!r} (continuing)")
    else:
        for stream in (completed.stdout, completed.stderr):
            if not stream:
                continue
            for line in stream.splitlines():
                print(f"[bootstrap] {line}")
        if completed.returncode == 0:
            print("[bootstrap] OK: Hermes skills wiring verified")
        else:
            print(
                "[bootstrap] WARNING: setup_hermes_skills.py exited with code "
                f"{completed.returncode} (continuing)"
            )

    return os.environ.get("QMCLAW_BOOTSTRAP_ONLY", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def main():
    global processes, to_start, process_info

    # 注册信号处理器
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # 创建输出队列
    output_queue = queue.Queue()

    # 确定要启动哪些服务
    args = sys.argv[1:] if len(sys.argv) > 1 else ["--all"]

    if "--all" in args:
        # 启动所有服务
        config = load_config()
        to_start = [name for name in SERVICES.keys()]
    else:
        # 只启动指定的服务
        to_start = [arg for arg in args if arg in SERVICES]

    if not to_start:
        print("No services to start. Use --all or specify service names.")
        print(f"Available services: {', '.join(SERVICES.keys())}")
        return

    # 启动任何服务之前先自举 Hermes 技能接线（幂等、失败不阻断）
    if run_hermes_skills_bootstrap():
        print("[bootstrap] QMCLAW_BOOTSTRAP_ONLY is set - skipping service startup")
        return

    print("=" * 60)
    print("QMClaw Services Starter")
    print("=" * 60)
    print(f"Services to start: {', '.join(to_start)}")
    print()

    # 启动服务
    config = load_config()
    for name in to_start:
        svc_config = config.get(name, SERVICES.get(name, {}))
        if not svc_config.get("enabled", True):
            print(f"[start_all] {name}: Disabled in config")
            continue

        proc = start_service(name, svc_config, output_queue)
        if proc:
            processes.append(proc)

    if not processes:
        print("[start_all] No services started")
        return

    # 等待服务就绪
    wait_for_services()

    print()
    print("=" * 60)
    print("All services started. Press Ctrl+C to stop.")
    print("=" * 60)

    # 启动输出打印线程
    error_counts: dict[str, int] = {}   # 每个服务的**真错误**条数
    warn_counts: dict[str, int] = {}

    def output_printer():
        """定期从队列中取出输出并打印。

        只有崩溃级的输出才标 [ERR]；warning 标 [WARN]；其余（绝大多数是服务
        自己的正常运行日志，因为本项目把日志写在 stderr）原样打印，不加标签。
        """
        while True:
            try:
                msg_name, is_error, line = output_queue.get(timeout=0.1)
                kind = classify_output_line(line) if is_error else "log"
                if kind == "error":
                    error_counts[msg_name] = error_counts.get(msg_name, 0) + 1
                elif kind == "warn":
                    warn_counts[msg_name] = warn_counts.get(msg_name, 0) + 1
                print(format_output_line(msg_name, is_error, line))
            except queue.Empty:
                continue

    printer_thread = threading.Thread(target=output_printer, daemon=True)
    printer_thread.start()

    def print_error_summary(when: str = ""):
        """汇总**真错误**条数——终端上一眼能看出到底有没有出问题。"""
        total = sum(error_counts.values())
        if total == 0:
            print(f"[start_all] 📋 真错误：0 条{when}（[ERR] 只在崩溃时出现；"
                  f"服务自己的日志走 stderr，但那都是正常的）")
            return
        detail = "、".join(f"{k}={v}" for k, v in sorted(error_counts.items()) if v)
        print(f"[start_all] 📋 真错误：{total} 条{when}（{detail}）—— 上面带 [ERR] 的就是")

    # 注意：打印线程是在 wait_for_services 之后才起的（启动期间的输出都堆在队列里），
    # 所以汇总必须等队列排空后再打 —— 也就是退出时、或某个进程异常退出时。

    # 保持运行，监控进程状态
    reported_exits = set()
    try:
        # 等待所有进程
        while True:
            for proc in processes:
                if proc and proc.poll() is not None and proc.pid not in reported_exits:
                    # 进程已退出：同一 PID 只报一次（poll() 会一直返回非 None）
                    reported_exits.add(proc.pid)
                    returncode = proc.returncode
                    print(f"\n[start_all] ⚠️  Process {proc.pid} exited with code {returncode}")
                    print(f"[start_all] Please check the output above for errors")
                    print_error_summary("（截至此刻）")

            # 检查是否所有进程都退出了
            if all(p.poll() is not None for p in processes if p):
                print("[start_all] All processes have exited")
                break

            time.sleep(1)

    except KeyboardInterrupt:
        print("\n[start_all] Interrupted")
    finally:
        stop_all()
        # 给打印线程一点时间把队列排空，再汇总
        time.sleep(0.5)
        print_error_summary("（本次运行）")


if __name__ == "__main__":
    main()
