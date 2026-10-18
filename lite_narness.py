#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
lite_narness — 极简 LLM 命令行对话工具 (TUI)

设计目标：配置外置、启动简单、够用即可。

依赖：仅 Python 标准库（网络使用 urllib），无第三方运行时依赖。

用法：
    lite_narness                  使用 config.json 中的默认模型
    lite_narness --model <名称>   临时覆盖默认模型
    lite_narness --config <路径>  指定配置文件
    lite_narness --prompt <路径>  指定系统提示词文件

配置与提示词均在“当前工作目录”读取/创建。API Key 不会被打印。
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.request

APP = "lite_narness"
VERSION = "1.0.0"
CONFIG_NAME = "config.json"
CONFIG_EXAMPLE = "config.example.json"
PROMPT_NAME = "system_prompt.md"

DEFAULT_SYSTEM_PROMPT = """你是一个简洁、准确、乐于助人的 AI 助手。

- 使用与用户相同的语言回答。
- 回答直接、切题，避免空话与冗余。
- 涉及代码时给出可运行的示例，并简要说明。
"""

# --------------------------------------------------------------------------- #
# 终端样式（TUI）
# --------------------------------------------------------------------------- #
class C:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    GRAY = "\033[90m"


def _enable_ansi() -> None:
    """在 Windows 控制台开启 ANSI/VT 转义支持。"""
    if os.name != "nt":
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        # ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
        for handle in (-11, -12):  # stdout, stderr
            h = kernel32.GetStdHandle(handle)
            mode = ctypes.c_uint32()
            if kernel32.GetConsoleMode(h, ctypes.byref(mode)):
                kernel32.SetConsoleMode(h, mode.value | 0x0004)
    except Exception:
        pass


def _setup_io() -> None:
    """统一使用 UTF-8，避免中文乱码。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    _enable_ansi()


def _use_color() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if not getattr(sys.stdout, "isatty", lambda: False)():
        return False
    return True


COLOR = True


def style(text: str, *codes: str) -> str:
    if not COLOR or not codes:
        return text
    return "".join(codes) + text + C.RESET


# --------------------------------------------------------------------------- #
# 配置与提示词
# --------------------------------------------------------------------------- #
class HarnessError(Exception):
    """面向用户的、可读的错误。"""


def _read_json(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        raise HarnessError(
            f"未找到配置文件：{path}\n"
            f"  修复：复制示例配置 {CONFIG_EXAMPLE} 为 {os.path.basename(path)}，"
            "然后填入 base_url / api_key / model。"
        )
    except json.JSONDecodeError as e:
        raise HarnessError(f"配置文件不是合法 JSON：{path}\n  详情：{e}")


def load_config(path: str) -> dict:
    cfg = _read_json(path)
    if not isinstance(cfg, dict):
        raise HarnessError(f"配置文件顶层必须是 JSON 对象：{path}")

    base_url = str(cfg.get("base_url") or "").strip()
    api_key = str(cfg.get("api_key") or "").strip()
    model = str(cfg.get("model") or "").strip()

    problems = []
    if not base_url:
        problems.append("base_url 为空")
    if not api_key:
        problems.append("api_key 为空")
    if not model:
        problems.append("model 为空")
    if problems:
        raise HarnessError(
            "配置不完整（" + "、".join(problems) + "）。\n"
            f"  修复：编辑 {os.path.basename(path)}，补齐 base_url / api_key / model。\n"
            "  参考模板：" + CONFIG_EXAMPLE + "（不要在模板里填写真实 Key）。"
        )

    cfg["base_url"] = base_url
    cfg["api_key"] = api_key
    cfg["model"] = model
    return cfg


def load_system_prompt(path: str) -> str:
    if not os.path.exists(path):
        # 缺失时给出清晰提示，并创建一份默认提示词。
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(DEFAULT_SYSTEM_PROMPT)
            print(style(f"[提示] 未找到 {path}，已创建默认系统提示词。", C.YELLOW))
            print(style("       可直接编辑该文件，重启后生效。", C.GRAY))
        except OSError as e:
            raise HarnessError(
                f"未找到且无法创建系统提示词文件：{path}\n  详情：{e}"
            )
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except OSError as e:
        raise HarnessError(f"无法读取系统提示词文件：{path}\n  详情：{e}")


# --------------------------------------------------------------------------- #
# API 调用
# --------------------------------------------------------------------------- #
def build_endpoint(base_url: str) -> str:
    url = base_url.rstrip("/")
    if url.endswith("/chat/completions"):
        return url
    return url + "/chat/completions"


def call_api(cfg: dict, model: str, messages: list) -> str:
    """调用 OpenAI 兼容的 /chat/completions。失败时抛出 HarnessError。"""
    url = build_endpoint(cfg["base_url"])
    payload = {"model": model, "messages": messages, "stream": False}
    if cfg.get("temperature") is not None:
        payload["temperature"] = cfg["temperature"]

    headers = {
        "Content-Type": "application/json",
        "Authorization": "Bearer " + cfg["api_key"],
    }
    for k, v in (cfg.get("headers") or {}).items():
        headers[str(k)] = str(v)

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")

    timeout = float(cfg.get("timeout", 60))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        # 注意：不回显请求头，避免泄露 API Key。
        detail = ""
        try:
            body = e.read().decode("utf-8", errors="replace")
            obj = json.loads(body)
            detail = str(
                (obj.get("error") or {}).get("message")
                if isinstance(obj.get("error"), dict)
                else obj.get("error") or obj.get("message") or ""
            )
        except Exception:
            detail = ""
        msg = f"服务返回错误：HTTP {e.code} {e.reason}"
        if detail:
            msg += f"\n  服务端信息：{detail[:400]}"
        raise HarnessError(msg)
    except urllib.error.URLError as e:
        raise HarnessError(
            f"网络错误：无法连接 {url}\n  详情：{e.reason}\n"
            "  请检查 base_url 是否正确、网络是否可达。"
        )
    except socket.timeout:
        raise HarnessError(f"请求超时（>{timeout:.0f}s）。可在配置中调大 timeout。")
    except ValueError as e:
        raise HarnessError(f"请求地址无效：{url}\n  详情：{e}")

    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        raise HarnessError("响应不是合法 JSON，可能地址错误或服务端异常。")

    try:
        choice = obj["choices"][0]
        content = choice["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise HarnessError("响应结构异常：未找到 choices[0].message.content。")
    if content is None:
        content = ""
    return str(content)


# --------------------------------------------------------------------------- #
# 输出辅助
# --------------------------------------------------------------------------- #
_spinner_lock = threading.Lock()


def _spinner(stop: threading.Event, text: str) -> None:
    frames = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
    i = 0
    while not stop.is_set():
        with _spinner_lock:
            sys.stdout.write("\r\033[K" + style(f"{frames[i % len(frames)]} {text}", C.DIM))
            sys.stdout.flush()
        i += 1
        time.sleep(0.08)
    with _spinner_lock:
        sys.stdout.write("\r\033[K")
        sys.stdout.flush()


def print_block(prefix: str, text: str, color: str) -> None:
    """带左侧色条的多行输出。"""
    lines = (text if text else "(空回复)").splitlines() or [""]
    label = style(prefix, C.BOLD, color)
    bar = style("│", C.GRAY)
    for idx, line in enumerate(lines):
        head = label if idx == 0 else " " * len(prefix)
        sys.stdout.write(bar + " " + head + " " + line + "\n")
    sys.stdout.flush()


def print_error(message: str) -> None:
    sys.stdout.write(style("✗ ", C.RED) + style(message, C.RED) + "\n")
    sys.stdout.flush()


def print_info(message: str) -> None:
    sys.stdout.write(style("• " + message, C.GRAY) + "\n")
    sys.stdout.flush()


def print_banner(model: str, endpoint: str, prompt_len: int) -> None:
    width = 54
    sys.stdout.write("\n")
    sys.stdout.write(style("╭" + "─" * width + "╮", C.MAGENTA) + "\n")
    title = f"  {APP} v{VERSION}"
    sub = "  极简 LLM 对话  ·  输入 /help 查看命令"
    sys.stdout.write(
        style("│", C.MAGENTA) + title.ljust(width) + style("│", C.MAGENTA) + "\n"
    )
    sys.stdout.write(
        style("│", C.MAGENTA)
        + style(sub.ljust(width), C.DIM)
        + style("│", C.MAGENTA)
        + "\n"
    )
    sys.stdout.write(style("╰" + "─" * width + "╯", C.MAGENTA) + "\n")
    print_info(f"模型: {model}")
    print_info(f"接口: {endpoint}")
    print_info(f"系统提示词: {prompt_len} 字符")
    sys.stdout.write("\n")


HELP_TEXT = """可用命令（直接输入普通文本即可对话）：
  /help            显示本帮助
  /model           显示当前模型
  /model <名称>    临时切换模型（仅本次会话）
  /models          列出配置中的可选模型
  /clear           清空对话历史（保留系统提示词）
  /prompt          显示当前系统提示词
  exit | quit      退出（也可按 Ctrl+C / Ctrl+D）"""


# --------------------------------------------------------------------------- #
# 主循环
# --------------------------------------------------------------------------- #
def run_repl(cfg: dict, model: str, system_prompt: str) -> int:
    history = []
    if system_prompt:
        history.append({"role": "system", "content": system_prompt})

    endpoint = build_endpoint(cfg["base_url"])
    print_banner(model, endpoint, len(system_prompt))

    prompt_mark = style("你 › ", C.BOLD, C.GREEN)
    while True:
        try:
            line = input(prompt_mark)
        except (EOFError, KeyboardInterrupt):
            sys.stdout.write("\n")
            break

        text = line.strip()
        if not text:
            continue
        if text in ("exit", "quit"):
            break

        if text.startswith("/"):
            parts = text.split(maxsplit=1)
            cmd = parts[0].lower()
            arg = parts[1].strip() if len(parts) > 1 else ""
            if cmd == "/help":
                sys.stdout.write(HELP_TEXT + "\n")
            elif cmd == "/model":
                if arg:
                    model = arg
                    print_info(f"已切换模型: {model}")
                else:
                    print_info(f"当前模型: {model}")
            elif cmd == "/models":
                models = cfg.get("models") or []
                if models:
                    for m in models:
                        mark = "  ← 当前" if m == model else ""
                        print_info(f"{m}{mark}")
                else:
                    print_info("配置中未提供 models 列表。")
            elif cmd == "/clear":
                history = [h for h in history if h["role"] == "system"]
                print_info("对话历史已清空。")
            elif cmd == "/prompt":
                print_block("sys", system_prompt or "(空)", C.CYAN)
            elif cmd in ("/exit", "/quit"):
                break
            else:
                print_error(f"未知命令：{cmd}（输入 /help 查看）")
            continue

        history.append({"role": "user", "content": text})
        stop = threading.Event()
        spin = threading.Thread(target=_spinner, args=(stop, "思考中…"), daemon=True)
        spin.start()
        try:
            reply = call_api(cfg, model, history)
        except HarnessError as e:
            stop.set()
            spin.join()
            print_error(str(e))
            history.pop()  # 移除本轮失败的输入
            continue
        except Exception as e:  # 兜底，不泄露内部细节
            stop.set()
            spin.join()
            print_error(f"发生未预期错误：{type(e).__name__}")
            history.pop()
            continue
        stop.set()
        spin.join()

        history.append({"role": "assistant", "content": reply})
        print_block("AI › ", reply, C.BLUE)
        sys.stdout.write("\n")
        sys.stdout.flush()

    print_info("再见。")
    return 0


def parse_args(argv) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog=APP,
        description="极简 LLM 命令行对话工具（配置外置，TUI）。",
    )
    p.add_argument("--model", "-m", help="临时覆盖配置中的默认模型")
    p.add_argument("--config", "-c", default=CONFIG_NAME, help=f"配置文件路径（默认 {CONFIG_NAME}）")
    p.add_argument("--prompt", "-p", default=PROMPT_NAME, help=f"系统提示词文件路径（默认 {PROMPT_NAME}）")
    p.add_argument("--version", "-V", action="version", version=f"{APP} {VERSION}")
    return p.parse_args(argv)


def main(argv=None) -> int:
    global COLOR
    args = parse_args(sys.argv[1:] if argv is None else argv)
    _setup_io()
    COLOR = _use_color()

    try:
        cfg = load_config(args.config)
        system_prompt = load_system_prompt(args.prompt)
    except HarnessError as e:
        print_error(str(e))
        return 1

    model = args.model.strip() if args.model else cfg["model"]
    if not model:
        print_error("模型未指定：请设置配置中的 model 或使用 --model。")
        return 1

    return run_repl(cfg, model, system_prompt)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
