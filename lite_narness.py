#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
lite_narness — 极简 LLM 命令行对话工具 (TUI)

设计目标：配置外置、启动简单、够用即可。

依赖：prompt_toolkit（TUI：全屏布局 / Tab 补全 / 鼠标点击 / 配置界面）。
      网络、流式解析、工具执行均使用 Python 标准库。

功能：
- 交互式多轮对话，支持流式输出与 tokens/s 统计
- OpenAI 兼容的 function calling（内置工具：run_shell / read_file / write_file）
- 全屏 TUI：左侧对话区 + 右侧 Context 侧栏 + 底部状态栏
- 应用内配置界面（/config），配置缺失时自动创建空配置并进入配置界面

用法：
    lite_narness                  使用 config.json 中的默认模型
    lite_narness --model <名称>   临时覆盖默认模型
    lite_narness --config <路径>  指定配置文件
    lite_narness --prompt <路径>  指定系统提示词文件

安全：API Key 不会被打印到终端；工具为全自动执行，请谨慎使用。
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import threading
import time
import unicodedata
import urllib.error
import urllib.request

APP = "lite_narness"
VERSION = "3.0.0"
CONFIG_NAME = "config.json"
CONFIG_EXAMPLE = "config.example.json"
PROMPT_NAME = "system_prompt.md"

REQUIRED_KEYS = ("base_url", "api_key", "model")
CONFIG_KEYS = (
    "base_url", "api_key", "model", "models",
    "temperature", "timeout", "context_window", "include_usage",
)

EMPTY_CONFIG = {
    "base_url": "",
    "api_key": "",
    "model": "",
    "models": [],
    "temperature": 0.7,
    "timeout": 60,
    "context_window": 128000,
    "include_usage": False,
}

DEFAULT_SYSTEM_PROMPT = """你是一个简洁、准确、乐于助人的 AI 助手。

- 使用与用户相同的语言回答。
- 回答直接、切题，避免空话与冗余。
- 需要时可以使用提供的工具来运行命令或读写文件。
"""

COMMANDS = {
    "/help": "显示帮助",
    "/model": "显示模型；/model <名称> 临时切换",
    "/models": "列出配置中的可选模型",
    "/clear": "清空对话历史（保留系统提示词）",
    "/prompt": "显示当前系统提示词",
    "/config": "打开配置界面（可修改并保存）",
    "/exit": "退出",
    "/quit": "退出",
}

HELP_TEXT = """可直接输入普通文本对话；输入 / 时 Tab 补全，可用鼠标点击补全项。
  /help            显示本帮助
  /model           显示当前模型
  /model <名称>    临时切换模型（仅本次会话）
  /models          列出配置中的可选模型
  /clear           清空对话历史（保留系统提示词）
  /prompt          显示当前系统提示词
  /config          打开配置界面，可修改并保存
  exit | quit      退出（也可 Ctrl+C / Ctrl+D）
快捷键：Enter 发送 · Ctrl+L 清屏 · Ctrl+P 帮助 · Ctrl+C 退出"""

# 内置工具（OpenAI 兼容 function calling schema）
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "run_shell",
            "description": "在当前工作目录执行一条 shell 命令，返回退出码与标准输出/错误。",
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string", "description": "要执行的命令"}},
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读取一个文本文件的内容。",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "文件路径"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "把内容写入一个文件（覆盖写入，自动创建父目录）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件路径"},
                    "content": {"type": "string", "description": "要写入的内容"},
                },
                "required": ["path", "content"],
            },
        },
    },
]

MAX_TOOL_OUTPUT = 20000

# --------------------------------------------------------------------------- #
# 可选依赖 prompt_toolkit
# --------------------------------------------------------------------------- #
try:
    from prompt_toolkit import Application, print_formatted_text
    from prompt_toolkit.application.current import get_app
    from prompt_toolkit.buffer import Buffer
    from prompt_toolkit.completion import Completer, Completion
    from prompt_toolkit.formatted_text import FormattedText
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.key_binding.defaults import load_key_bindings
    from prompt_toolkit.key_binding.key_bindings import merge_key_bindings
    from prompt_toolkit.layout import HSplit, Layout, VSplit, Window, WindowAlign
    from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
    from prompt_toolkit.layout.dimension import Dimension
    from prompt_toolkit.layout.margins import ScrollbarMargin
    from prompt_toolkit.shortcuts import CompleteStyle, message_dialog, radiolist_dialog
    from prompt_toolkit.styles import Style
    from prompt_toolkit.widgets import Button, Dialog, Label, TextArea

    HAVE_PT = True
    STYLE = Style.from_dict(
        {
            "header": "bg:#1e1e2e #cdd6f4",
            "header.title": "bg:#1e1e2e #f5c2e7 bold",
            "header.dim": "bg:#1e1e2e #6c7086",
            "chat": "bg:#11111b #cdd6f4",
            "chat.role.user": "bg:#11111b #89b4fa bold",
            "chat.role.ai": "bg:#11111b #a6e3a1 bold",
            "chat.role.tool": "bg:#11111b #f9e2af bold",
            "chat.tool": "bg:#11111b #9399b2",
            "chat.dim": "bg:#11111b #6c7086",
            "chat.error": "bg:#11111b #f38ba8 bold",
            "sidebar": "bg:#181825 #cdd6f4",
            "sidebar.title": "bg:#181825 #f5c2e7 bold",
            "sidebar.label": "bg:#181825 #6c7086",
            "sidebar.value": "bg:#181825 #a6e3a1",
            "separator": "#313244",
            "status": "bg:#181825 #9399b2",
            "status.model": "bg:#181825 #89b4fa bold",
            "footer": "bg:#181825 #6c7086",
            "footer.dim": "bg:#181825 #585b70",
            "inputarea": "bg:#1e1e2e #cdd6f4",
            "input.prompt": "bg:#1e1e2e #a6e3a1 bold",
            "completion-menu.completion": "bg:#313244 #cdd6f4",
            "completion-menu.completion.current": "bg:#89b4fa #1e1e2e",
        }
    )
except ImportError:  # pragma: no cover - 降级到纯文本模式
    HAVE_PT = False
    STYLE = None


class HarnessError(Exception):
    """面向用户的、可读的错误。"""


# --------------------------------------------------------------------------- #
# 纯文本输出（用于 TUI 启动前 / 非交互环境）
# --------------------------------------------------------------------------- #
def _color_enabled() -> bool:
    return bool(getattr(sys.stdout, "isatty", lambda: False)()) and not os.environ.get(
        "NO_COLOR"
    )


def _ansi(code: str, text: str) -> str:
    if not _color_enabled():
        return text
    return f"\033[{code}m{text}\033[0m"


def emit_error(message: str) -> None:
    sys.stdout.write(_ansi("31", "✗ ") + _ansi("31", message) + "\n")
    sys.stdout.flush()


def emit_info(message: str) -> None:
    sys.stdout.write(_ansi("90", "• " + message) + "\n")
    sys.stdout.flush()


def _display_width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)


def _pad(text: str, width: int) -> str:
    return text + " " * max(0, width - _display_width(text))


# --------------------------------------------------------------------------- #
# 配置读写
# --------------------------------------------------------------------------- #
def write_config(path: str, cfg: dict) -> None:
    data = {k: cfg.get(k, EMPTY_CONFIG[k]) for k in CONFIG_KEYS}
    for k, v in cfg.items():
        data.setdefault(k, v)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def read_config(path: str) -> tuple[dict, bool]:
    """读取配置。缺失时自动创建空配置模板。返回 (配置, 是否已存在)。"""
    if not os.path.exists(path):
        try:
            write_config(path, dict(EMPTY_CONFIG))
        except OSError as e:
            raise HarnessError(f"无法创建配置文件：{path}\n  详情：{e}")
        return dict(EMPTY_CONFIG), False
    try:
        with open(path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except json.JSONDecodeError as e:
        raise HarnessError(f"配置文件不是合法 JSON：{path}\n  详情：{e}")
    except OSError as e:
        raise HarnessError(f"无法读取配置文件：{path}\n  详情：{e}")
    if not isinstance(cfg, dict):
        raise HarnessError(f"配置文件顶层必须是 JSON 对象：{path}")
    models = cfg.get("models")
    if isinstance(models, str):
        cfg["models"] = [m.strip() for m in models.replace("，", ",").split(",") if m.strip()]
    elif models is not None and not isinstance(models, list):
        cfg["models"] = []
    return cfg, True


def config_problems(cfg: dict) -> list:
    return [k for k in REQUIRED_KEYS if not str(cfg.get(k) or "").strip()]


def config_error_text(path: str, problems: list) -> str:
    return (
        "配置不完整（" + "、".join(problems) + " 为空）。\n"
        f"  修复：编辑 {os.path.basename(path)}，补齐 base_url / api_key / model；\n"
        f"  或参考模板 {CONFIG_EXAMPLE}（不要在模板里填写真实 Key）。"
    )


def load_system_prompt(path: str) -> str:
    if not os.path.exists(path):
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(DEFAULT_SYSTEM_PROMPT)
            emit_info(f"未找到 {path}，已创建默认系统提示词（可编辑后重启生效）。")
        except OSError as e:
            raise HarnessError(f"未找到且无法创建系统提示词文件：{path}\n  详情：{e}")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except OSError as e:
        raise HarnessError(f"无法读取系统提示词文件：{path}\n  详情：{e}")


# --------------------------------------------------------------------------- #
# 内置工具
# --------------------------------------------------------------------------- #
def _truncate(text: str, limit: int = MAX_TOOL_OUTPUT) -> str:
    if len(text) > limit:
        return text[:limit] + f"\n…[已截断，共 {len(text)} 字符]"
    return text


def execute_tool(name: str, args_json: str) -> str:
    try:
        args = json.loads(args_json) if args_json else {}
        if not isinstance(args, dict):
            args = {}
    except json.JSONDecodeError as e:
        return f"工具参数不是合法 JSON：{e}"

    if name == "run_shell":
        command = str(args.get("command") or "")
        if not command:
            return "缺少 command 参数"
        try:
            proc = subprocess.run(
                command, shell=True, capture_output=True, text=True, timeout=120
            )
            out = (
                f"exit_code={proc.returncode}\n"
                f"--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}"
            )
            return _truncate(out)
        except subprocess.TimeoutExpired:
            return "命令执行超时（>120s）"
        except Exception as e:
            return f"执行失败：{type(e).__name__}: {e}"

    if name == "read_file":
        path = str(args.get("path") or "")
        if not path:
            return "缺少 path 参数"
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                data = f.read()
            return _truncate(data) if data else "(空文件)"
        except FileNotFoundError:
            return f"文件不存在：{path}"
        except IsADirectoryError:
            return f"是一个目录：{path}"
        except Exception as e:
            return f"读取失败：{type(e).__name__}: {e}"

    if name == "write_file":
        path = str(args.get("path") or "")
        content = str(args.get("content") or "")
        if not path:
            return "缺少 path 参数"
        try:
            parent = os.path.dirname(os.path.abspath(path))
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            return f"已写入 {path}（{len(content)} 字符）"
        except Exception as e:
            return f"写入失败：{type(e).__name__}: {e}"

    return f"未知工具：{name}"


# --------------------------------------------------------------------------- #
# API 调用（SSE 流式 + function calling）
# --------------------------------------------------------------------------- #
def build_endpoint(base_url: str) -> str:
    url = base_url.rstrip("/")
    if url.endswith("/chat/completions"):
        return url
    return url + "/chat/completions"


def _api_error(e: "urllib.error.HTTPError") -> HarnessError:
    detail = ""
    try:
        obj = json.loads(e.read().decode("utf-8", errors="replace"))
        err = obj.get("error")
        if isinstance(err, dict):
            detail = str(err.get("message") or "")
        else:
            detail = str(err or obj.get("message") or "")
    except Exception:
        detail = ""
    msg = f"服务返回错误：HTTP {e.code} {e.reason}"
    if detail:
        msg += f"\n  服务端信息：{detail[:400]}"
    return HarnessError(msg)


def stream_chat(cfg, model, messages, tools, on_delta, cancel_event=None):
    """调用 /chat/completions（流式）。返回 (content, tool_calls, usage)。"""
    url = build_endpoint(cfg["base_url"])
    payload = {"model": model, "messages": messages, "stream": True}
    if cfg.get("temperature") is not None:
        payload["temperature"] = cfg["temperature"]
    if tools:
        payload["tools"] = tools
    if cfg.get("include_usage"):
        payload["stream_options"] = {"include_usage": True}

    headers = {
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
        "Authorization": "Bearer " + cfg["api_key"],
    }
    for k, v in (cfg.get("headers") or {}).items():
        headers[str(k)] = str(v)

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    timeout = float(cfg.get("timeout", 60))

    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        raise _api_error(e)
    except urllib.error.URLError as e:
        raise HarnessError(
            f"网络错误：无法连接 {url}\n  详情：{e.reason}\n"
            "  请检查 base_url 是否正确、网络是否可达。"
        )
    except socket.timeout:
        raise HarnessError(f"请求超时（>{timeout:.0f}s）。可在配置中调大 timeout。")

    content_parts: list = []
    tool_slots: dict = {}
    usage = None

    def handle_obj(obj: dict) -> None:
        nonlocal usage
        if obj.get("usage"):
            usage = obj["usage"]
        choices = obj.get("choices") or []
        if not choices:
            return
        delta = choices[0].get("delta")
        if delta is None:
            delta = choices[0].get("message") or {}
        piece = delta.get("content")
        if piece:
            content_parts.append(piece)
            on_delta(piece)
        for tc in delta.get("tool_calls") or []:
            idx = tc.get("index", 0)
            slot = tool_slots.setdefault(idx, {"id": "", "name": "", "args": ""})
            if tc.get("id"):
                slot["id"] = tc["id"]
            fn = tc.get("function") or {}
            if fn.get("name"):
                slot["name"] += fn["name"]
            if fn.get("arguments"):
                slot["args"] += fn["arguments"]

    try:
        ctype = (resp.headers.get_content_type() or "").lower()
        if "event-stream" in ctype:
            for raw in resp:
                if cancel_event is not None and cancel_event.is_set():
                    break
                line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                if not line or line.startswith(":"):
                    continue
                if not line.startswith("data:"):
                    continue
                chunk = line[5:].strip()
                if chunk == "[DONE]":
                    break
                try:
                    handle_obj(json.loads(chunk))
                except json.JSONDecodeError:
                    continue
        else:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError:
                raise HarnessError("响应不是合法 JSON，可能地址错误或服务端异常。")
            if isinstance(obj.get("error"), (dict, str)):
                err = obj["error"]
                detail = err.get("message") if isinstance(err, dict) else err
                raise HarnessError(f"服务返回错误：{detail}")
            handle_obj(obj)
    finally:
        try:
            resp.close()
        except Exception:
            pass

    tool_calls = []
    for idx in sorted(tool_slots):
        slot = tool_slots[idx]
        tool_calls.append(
            {
                "id": slot["id"] or f"call_{idx}",
                "type": "function",
                "function": {
                    "name": slot["name"] or "unknown",
                    "arguments": slot["args"] or "{}",
                },
            }
        )
    if not content_parts and not tool_calls:
        raise HarnessError("响应结构异常：未获得任何内容或工具调用。")
    return "".join(content_parts), tool_calls, usage


def estimate_history_tokens(history: list) -> int:
    chars = sum(len(str(m.get("content") or "")) for m in history)
    chars += sum(len(json.dumps(m.get("tool_calls", []), ensure_ascii=False)) for m in history)
    return max(1, chars // 4)


# --------------------------------------------------------------------------- #
# Agent 循环（对话 + 工具调用），与 UI 解耦
# --------------------------------------------------------------------------- #
def agent_turn(cfg, state, history, emit, cancel_event=None):
    """执行一轮（含工具调用循环）。emit(event, data) 负责展示。"""
    while True:
        emit("assistant_start", None)
        first = {"t": None}

        def on_delta(piece):
            if first["t"] is None:
                first["t"] = time.time()
            emit("assistant_delta", piece)

        content, tool_calls, usage = stream_chat(
            cfg, state.model, history, TOOLS, on_delta, cancel_event
        )
        emit(
            "assistant_end",
            {"content": content, "start": first["t"], "has_tool_calls": bool(tool_calls)},
        )
        if usage and usage.get("total_tokens"):
            emit("usage", int(usage["total_tokens"]))

        if tool_calls:
            history.append(
                {"role": "assistant", "content": content or "", "tool_calls": tool_calls}
            )
        else:
            history.append({"role": "assistant", "content": content})
            break

        for tc in tool_calls:
            name = tc["function"]["name"]
            args = tc["function"]["arguments"]
            emit("tool_call", (name, args))
            result = execute_tool(name, args)
            emit("tool_result", result)
            history.append({"role": "tool", "tool_call_id": tc["id"], "content": result})

        if cancel_event is not None and cancel_event.is_set():
            break


# --------------------------------------------------------------------------- #
# 纯文本 UI（非交互降级）
# --------------------------------------------------------------------------- #
class PlainUI:
    def __init__(self):
        self._ai_started = False

    def emit(self, event, data):
        if event == "assistant_start":
            self._ai_started = False
        elif event == "assistant_delta":
            if not self._ai_started:
                sys.stdout.write("\n" + _ansi("1;34", "AI › "))
                self._ai_started = True
            sys.stdout.write(data)
            sys.stdout.flush()
        elif event == "assistant_end":
            if not self._ai_started:
                if data.get("content"):
                    sys.stdout.write("\n" + _ansi("1;34", "AI › ") + data["content"])
                elif not data.get("has_tool_calls"):
                    sys.stdout.write("\n" + _ansi("1;34", "AI › ") + _ansi("90", "(空回复)"))
            sys.stdout.write("\n")
            sys.stdout.flush()
        elif event == "tool_call":
            name, args = data
            sys.stdout.write(_ansi("33", f"\n⚙ {name} {args}\n"))
            sys.stdout.flush()
        elif event == "tool_result":
            for line in str(data).splitlines()[:30]:
                sys.stdout.write(_ansi("90", "  " + line + "\n"))
            sys.stdout.flush()
        elif event == "usage":
            pass

    def user(self, text):
        sys.stdout.write(_ansi("1;32", "你 › ") + text + "\n")

    def info(self, text):
        emit_info(text)

    def error(self, text):
        emit_error(text)


def run_plain(cfg, state, system_prompt, config_path):
    history = [{"role": "system", "content": system_prompt}] if system_prompt else []
    ui = PlainUI()
    context_window = int(cfg.get("context_window") or 128000)
    tty = bool(getattr(sys.stdin, "isatty", lambda: False)())
    prompt = _ansi("1;32", "你 › ") if tty else ""

    while True:
        try:
            line = input(prompt)
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
                print(HELP_TEXT)
            elif cmd == "/model":
                if arg:
                    state.model = arg
                    ui.info(f"已切换模型: {state.model}")
                else:
                    ui.info(f"当前模型: {state.model}")
            elif cmd == "/models":
                names = [m for m in (cfg.get("models") or []) if m]
                ui.info(", ".join(names) if names else "配置中未提供 models 列表。")
            elif cmd == "/clear":
                history = [h for h in history if h["role"] == "system"]
                ui.info("对话历史已清空。")
            elif cmd == "/prompt":
                print(system_prompt or "（空）")
            elif cmd == "/config":
                ui.info(f"当前环境不支持配置界面，请手动编辑 {config_path} 后重启。")
            elif cmd in ("/exit", "/quit"):
                break
            else:
                ui.error(f"未知命令：{cmd}（输入 /help 查看）")
            continue

        if not tty:
            ui.user(text)
        history.append({"role": "user", "content": text})
        try:
            agent_turn(cfg, state, history, ui.emit)
        except HarnessError as e:
            ui.error(str(e))
            history.pop()
        except Exception as e:
            ui.error(f"发生未预期错误：{type(e).__name__}")
            history.pop()
        used = estimate_history_tokens(history)
        pct = min(100, round(used * 100 / context_window)) if context_window else 0
        ui.info(f"context ≈ {used} tokens ({pct}%)")
    ui.info("再见。")
    return 0


# --------------------------------------------------------------------------- #
# 配置界面（鼠标可点击）
# --------------------------------------------------------------------------- #
def _masked_key(cfg: dict) -> str:
    return "••••••••（已设置）" if str(cfg.get("api_key") or "").strip() else "（空）"


def _config_values(cfg: dict) -> list:
    models = cfg.get("models")
    models_text = ", ".join(m for m in models if m) if isinstance(models, list) else ""
    return [
        ("base_url", f"base_url       {cfg.get('base_url') or '（空）'}"),
        ("api_key", f"api_key        {_masked_key(cfg)}"),
        ("model", f"model          {cfg.get('model') or '（空）'}"),
        ("models", f"models         {models_text or '（空）'}"),
        ("temperature", f"temperature    {cfg.get('temperature')}"),
        ("timeout", f"timeout        {cfg.get('timeout')}"),
        ("context_window", f"context_window {cfg.get('context_window')}"),
        ("include_usage", f"include_usage  {cfg.get('include_usage')}"),
    ]


if HAVE_PT:
    from prompt_toolkit.application import Application  # noqa: F811

    def text_dialog(title, text="", default="", password=False):
        """单行文本输入对话框：回车即确认（也可鼠标点击“确定”）。"""

        def on_accept(buf):
            get_app().exit(result=buf.text)
            return True

        textfield = TextArea(
            text=default, multiline=False, password=password, accept_handler=on_accept
        )
        kb = KeyBindings()
        kb.add("escape", eager=True)(lambda e: e.app.exit(result=None))
        dialog = Dialog(
            title=title,
            body=HSplit([Label(text=text, dont_extend_height=True), textfield]),
            buttons=[
                Button(text="确定", handler=lambda: get_app().exit(result=textfield.text)),
                Button(text="取消", handler=lambda: get_app().exit(result=None)),
            ],
            with_background=True,
        )
        return Application(
            layout=Layout(dialog),
            key_bindings=merge_key_bindings([load_key_bindings(), kb]),
            mouse_support=True,
            full_screen=True,
            style=STYLE,
        )

    def edit_config(cfg):
        """打开配置界面。返回修改后的配置；取消则返回 None。"""
        cfg = dict(cfg)
        while True:
            values = _config_values(cfg) + [
                ("__save__", "✔ 保存并返回"),
                ("__cancel__", "✘ 取消"),
            ]
            choice = radiolist_dialog(
                title="lite_narness 配置",
                text="选择要修改的项（可鼠标点击），编辑完成后选择“保存并返回”。",
                values=values,
                ok_text="确定",
                cancel_text="取消",
            ).run()
            if choice is None or choice == "__cancel__":
                return None
            if choice == "__save__":
                missing = config_problems(cfg)
                if missing:
                    message_dialog(
                        title="配置不完整", text="请先填写：" + "、".join(missing)
                    ).run()
                    continue
                return cfg

            if choice == "base_url":
                v = text_dialog(
                    title="base_url",
                    text="API 服务地址（例如 https://api.example.com/v1）",
                    default=str(cfg.get("base_url") or ""),
                ).run()
                if v is not None:
                    cfg["base_url"] = v.strip()
            elif choice == "api_key":
                v = text_dialog(
                    title="api_key",
                    text="API Key（保存在配置文件中，请勿提交到版本库）",
                    default=str(cfg.get("api_key") or ""),
                    password=True,
                ).run()
                if v is not None:
                    cfg["api_key"] = v.strip()
            elif choice == "model":
                models = [m for m in (cfg.get("models") or []) if m]
                if models:
                    options = [(m, m) for m in models] + [("__custom__", "手动输入…")]
                    cur = cfg.get("model") if cfg.get("model") in models else None
                    v = radiolist_dialog(
                        title="选择默认模型",
                        text="从列表中选择（可鼠标点击），或手动输入。",
                        values=options,
                        default=cur,
                    ).run()
                    if v == "__custom__":
                        t = text_dialog(
                            title="model", text="输入模型名称", default=str(cfg.get("model") or "")
                        ).run()
                        if t is not None:
                            cfg["model"] = t.strip()
                    elif v is not None:
                        cfg["model"] = v
                else:
                    t = text_dialog(
                        title="model", text="输入模型名称", default=str(cfg.get("model") or "")
                    ).run()
                    if t is not None:
                        cfg["model"] = t.strip()
            elif choice == "models":
                t = text_dialog(
                    title="models",
                    text="可选模型列表，用逗号分隔",
                    default=", ".join(cfg.get("models") or []),
                ).run()
                if t is not None:
                    cfg["models"] = [
                        x.strip() for x in t.replace("，", ",").split(",") if x.strip()
                    ]
            elif choice == "temperature":
                cur = cfg.get("temperature")
                t = text_dialog(
                    title="temperature",
                    text="采样温度（如 0.7）；留空表示使用服务端默认",
                    default="" if cur is None else str(cur),
                ).run()
                if t is not None:
                    t = t.strip()
                    if t == "":
                        cfg.pop("temperature", None)
                    else:
                        try:
                            cfg["temperature"] = float(t)
                        except ValueError:
                            message_dialog(title="无效输入", text="temperature 必须是数字。").run()
            elif choice == "timeout":
                t = text_dialog(
                    title="timeout",
                    text="请求超时秒数（如 60）",
                    default=str(cfg.get("timeout", 60)),
                ).run()
                if t is not None:
                    t = t.strip()
                    try:
                        cfg["timeout"] = int(float(t)) if t else 60
                    except ValueError:
                        message_dialog(title="无效输入", text="timeout 必须是整数。").run()
            elif choice == "context_window":
                t = text_dialog(
                    title="context_window",
                    text="上下文窗口大小（用于统计百分比，如 128000）",
                    default=str(cfg.get("context_window", 128000)),
                ).run()
                if t is not None:
                    t = t.strip()
                    try:
                        cfg["context_window"] = int(float(t)) if t else 128000
                    except ValueError:
                        message_dialog(title="无效输入", text="context_window 必须是整数。").run()
            elif choice == "include_usage":
                cfg["include_usage"] = not bool(cfg.get("include_usage"))


# --------------------------------------------------------------------------- #
# 全屏 TUI
# --------------------------------------------------------------------------- #
class ModelState:
    """持有配置与当前模型（会话内可变）。"""

    def __init__(self, cfg: dict, model: str):
        self.cfg = cfg
        self.model = model

    def names(self) -> list:
        names = [m for m in (self.cfg.get("models") or []) if m]
        if self.model and self.model not in names:
            names.insert(0, self.model)
        return names


if HAVE_PT:

    class CommandCompleter(Completer):
        def __init__(self, state: ModelState):
            self.state = state

        def get_completions(self, document, complete_event):
            text = document.text_before_cursor
            if not text.startswith("/"):
                return
            if text.startswith("/model "):
                frag = text[len("/model "):]
                for name in self.state.names():
                    if name.startswith(frag):
                        yield Completion(name, start_position=-len(frag))
                return
            if " " in text:
                return
            for cmd, desc in COMMANDS.items():
                if cmd.startswith(text):
                    yield Completion(
                        cmd, start_position=-len(text), display=cmd, display_meta=desc
                    )

    class TuiApp:
        def __init__(self, cfg, state, system_prompt, config_path, input=None, output=None):
            self._input = input
            self._output = output
            self.cfg = cfg
            self.state = state
            self.config_path = config_path
            self.history = [{"role": "system", "content": system_prompt}] if system_prompt else []
            self.transcript = []
            self.busy = False
            self.status = "就绪"
            self.context_window = int(cfg.get("context_window") or 128000)
            self.context_tokens = 0
            self.cwd = os.getcwd()
            self._stream_chars = 0
            self._ai_index = 0

            self._build()

        # ---- 组件构建 ----
        def _build(self):
            self.input_buffer = Buffer(
                multiline=False,
                completer=CommandCompleter(self.state),
                complete_while_typing=True,
                accept_handler=self._on_accept,
            )

            def input_prefix(wrap_count, line_number):
                if line_number == 0:
                    return FormattedText([("class:input.prompt", "你 › ")])
                return FormattedText([("", "     ")])

            self.input_window = Window(
                BufferControl(buffer=self.input_buffer),
                get_line_prefix=input_prefix,
                wrap_lines=True,
                height=Dimension(min=1, max=5),
                style="class:inputarea",
            )

            self.chat_window = Window(
                FormattedTextControl(self._chat_text),
                wrap_lines=True,
                right_margins=[ScrollbarMargin(display_arrows=True)],
                style="class:chat",
            )

            self.layout = Layout(
                HSplit(
                    [
                        Window(FormattedTextControl(self._header_text), height=1, style="class:header"),
                        VSplit(
                            [
                                self.chat_window,
                                Window(width=1, char="│", style="class:separator"),
                                Window(
                                    FormattedTextControl(self._sidebar_text),
                                    width=34,
                                    style="class:sidebar",
                                ),
                            ]
                        ),
                        Window(height=1, char="─", style="class:separator"),
                        Window(FormattedTextControl(self._status_text), height=1, style="class:status"),
                        self.input_window,
                        Window(height=1, char="─", style="class:separator"),
                        VSplit(
                            [
                                Window(FormattedTextControl(self._footer_left), align=WindowAlign.LEFT, style="class:footer"),
                                Window(FormattedTextControl(self._footer_right), align=WindowAlign.RIGHT, style="class:footer"),
                            ],
                            height=1,
                        ),
                    ]
                ),
                focused_element=self.input_window,
            )

            kb = KeyBindings()

            @kb.add("c-c")
            def _(event):
                event.app.exit()

            @kb.add("c-d")
            def _(event):
                if not self.input_buffer.text.strip():
                    event.app.exit()

            @kb.add("c-l")
            def _(event):
                self.transcript = []
                self._redraw()

            @kb.add("c-p")
            def _(event):
                self._add_info(HELP_TEXT)

            self.app = Application(
                layout=self.layout,
                key_bindings=kb,
                full_screen=True,
                mouse_support=True,
                style=STYLE,
                input=self._input,
                output=self._output,
            )

        # ---- 文本生成 ----
        def _header_text(self):
            return FormattedText(
                [
                    ("class:header", " "),
                    ("class:header.title", APP),
                    ("class:header.dim", f"  {VERSION}"),
                    ("class:header.dim", "   ·   "),
                    ("class:header", self.state.model),
                ]
            )

        def _chat_text(self):
            return self.transcript

        def _sidebar_text(self):
            pct = 0
            if self.context_window:
                pct = min(100, round(self.context_tokens * 100 / self.context_window))
            return FormattedText(
                [
                    ("class:sidebar.title", " Context\n"),
                    ("class:sidebar.label", " 模型\n"),
                    ("class:sidebar.value", f" {self.state.model}\n"),
                    ("", "\n"),
                    ("class:sidebar.label", " 上下文用量\n"),
                    ("class:sidebar.value", f" {self.context_tokens:,} tokens\n"),
                    ("class:sidebar.value", f" {pct}% / {self.context_window:,}\n"),
                    ("", "\n"),
                    ("class:sidebar.label", " 会话\n"),
                    ("class:sidebar.label", f" 消息 {max(0, len(self.history) - 1)}\n"),
                    ("class:sidebar.label", f" 状态 {self.status}\n"),
                    ("", "\n"),
                    ("class:sidebar.label", " 工具\n"),
                    ("class:sidebar.label", " run_shell\n"),
                    ("class:sidebar.label", " read_file\n"),
                    ("class:sidebar.label", " write_file\n"),
                ]
            )

        def _status_text(self):
            return FormattedText(
                [
                    ("class:status.model", f" {self.state.model}"),
                    ("class:status", f"  ·  {self.status}"),
                ]
            )

        def _footer_left(self):
            return FormattedText([("class:footer", f" {self.cwd} ")])

        def _footer_right(self):
            pct = 0
            if self.context_window:
                pct = min(100, round(self.context_tokens * 100 / self.context_window))
            return FormattedText(
                [("class:footer", f"{self.context_tokens:,} ({pct}%)   ctrl+p 命令   ctrl+c 退出 ")]
            )

        # ---- transcript 操作 ----
        def _append(self, *fragments):
            self.transcript.extend(fragments)

        def _add_user(self, text):
            self._append(
                ("class:chat.role.user", "你"),
                ("class:chat", " " + text + "\n"),
            )

        def _add_ai_start(self):
            self._append(("class:chat.role.ai", "AI"), ("class:chat", " "))

        def _add_delta(self, piece):
            if not piece:
                return
            if self.transcript and self.transcript[-1][0] == "class:chat":
                self.transcript[-1] = ("class:chat", self.transcript[-1][1] + piece)
            else:
                self._append(("class:chat", piece))
            self._stream_chars += len(piece)

        def _add_ai_end(self, empty):
            if empty:
                self._append(("class:chat.dim", "(空回复)"))
            self._append(("class:chat", "\n\n"))

        def _add_tool(self, name, args):
            self._append(
                ("class:chat.role.tool", f"⚙ {name}"),
                ("class:chat.dim", f" {args}\n"),
            )

        def _add_tool_result(self, text):
            lines = str(text).splitlines() or [""]
            shown = lines[:20]
            for line in shown:
                self._append(("class:chat.tool", "  " + line + "\n"))
            if len(lines) > len(shown):
                self._append(("class:chat.dim", f"  …（其余 {len(lines) - len(shown)} 行省略）\n"))
            self._append(("class:chat", "\n"))

        def _add_info(self, text):
            for line in text.splitlines():
                self._append(("class:chat.dim", "• " + line + "\n"))

        def _add_error(self, text):
            for line in str(text).splitlines():
                self._append(("class:chat.error", "✗ " + line + "\n"))

        def _redraw(self):
            try:
                self.chat_window.vertical_scroll = 1_000_000
            except Exception:
                pass
            try:
                self.app.invalidate()
            except Exception:
                pass

        # ---- 事件（可能来自工作线程）----
        def _emit(self, event, data):
            def do():
                self._apply_event(event, data)
                self._redraw()

            try:
                self.app.loop.call_soon_threadsafe(do)
            except Exception:
                do()

        def _apply_event(self, event, data):
            if event == "error":
                self._add_error(data)
                self.status = "就绪"
                return
            if event == "assistant_start":
                self.status = "生成中…"
                self._stream_chars = 0
                self._start_time = time.time()
                self._ai_index = len(self.transcript)
                self._add_ai_start()
            elif event == "assistant_delta":
                self._add_delta(data)
            elif event == "assistant_end":
                content = data.get("content") or ""
                if not content and data.get("has_tool_calls"):
                    del self.transcript[self._ai_index :]
                else:
                    self._add_ai_end(not content)
                start = data.get("start")
                if start and self._stream_chars:
                    secs = max(0.001, time.time() - start)
                    rate = (self._stream_chars / 4) / secs
                    self.status = f"就绪 · 上次 {rate:.1f} tok/s"
                else:
                    self.status = "就绪"
                self.context_tokens = estimate_history_tokens(self.history)
            elif event == "tool_call":
                name, args = data
                self.status = f"调用工具 {name}…"
                self._add_tool(name, args)
            elif event == "tool_result":
                self._add_tool_result(data)
                self.context_tokens = estimate_history_tokens(self.history)
            elif event == "usage":
                self.context_tokens = int(data)

        # ---- 输入处理 ----
        def _on_accept(self, buffer):
            text = buffer.text.strip()
            buffer.text = ""
            if text:
                self._submit(text)
            return True

        def _submit(self, text):
            if text in ("exit", "quit"):
                self.app.exit()
                return
            if text.startswith("/"):
                self._handle_command(text)
                self._redraw()
                return
            if self.busy:
                self._add_error("上一轮仍在进行中，请稍候。")
                self._redraw()
                return
            self.busy = True
            self._add_user(text)
            self._redraw()
            threading.Thread(target=self._worker, args=(text,), daemon=True).start()

        def _worker(self, text):
            self.history.append({"role": "user", "content": text})
            try:
                agent_turn(self.cfg, self.state, self.history, self._emit)
            except HarnessError as e:
                self._emit("error", str(e))
                if self.history and self.history[-1].get("role") == "user":
                    self.history.pop()
            except Exception as e:
                self._emit("error", f"发生未预期错误：{type(e).__name__}")
                if self.history and self.history[-1].get("role") == "user":
                    self.history.pop()
            finally:
                self.busy = False

        def _handle_command(self, text):
            parts = text.split(maxsplit=1)
            cmd = parts[0].lower()
            arg = parts[1].strip() if len(parts) > 1 else ""
            if cmd == "/help":
                self._add_info(HELP_TEXT)
            elif cmd == "/model":
                if arg:
                    self.state.model = arg
                    self._add_info(f"已切换模型: {self.state.model}")
                else:
                    self._add_info(f"当前模型: {self.state.model}")
            elif cmd == "/models":
                names = [m for m in (self.cfg.get("models") or []) if m]
                self._add_info((", ".join(names)) if names else "配置中未提供 models 列表。")
            elif cmd == "/clear":
                self.history = [h for h in self.history if h["role"] == "system"]
                self.transcript = []
                self.context_tokens = estimate_history_tokens(self.history)
                self._add_info("对话历史已清空。")
            elif cmd == "/prompt":
                sp = self.history[0]["content"] if self.history and self.history[0]["role"] == "system" else "（空）"
                self._add_info("系统提示词：\n" + sp)
            elif cmd == "/config":
                try:
                    with self.app.suspend():
                        newcfg = edit_config(self.cfg)
                except Exception as e:
                    self._add_error(f"无法打开配置界面：{type(e).__name__}: {e}")
                    return
                if newcfg:
                    try:
                        write_config(self.config_path, newcfg)
                    except OSError as e:
                        self._add_error(f"保存配置失败：{e}")
                        return
                    self.cfg.clear()
                    self.cfg.update(newcfg)
                    self.state.cfg = self.cfg
                    if str(newcfg.get("model") or "").strip():
                        self.state.model = str(newcfg["model"]).strip()
                    self.context_window = int(self.cfg.get("context_window") or 128000)
                    self._add_info("配置已保存并生效。")
                else:
                    self._add_info("已取消，配置未修改。")
            elif cmd in ("/exit", "/quit"):
                self.app.exit()
            else:
                self._add_error(f"未知命令：{cmd}（输入 /help 查看）")

        def run(self):
            return self.app.run()


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #
def is_interactive() -> bool:
    return (
        HAVE_PT
        and bool(getattr(sys.stdin, "isatty", lambda: False)())
        and bool(getattr(sys.stdout, "isatty", lambda: False)())
    )


def parse_args(argv) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog=APP, description="极简 LLM 命令行对话工具（TUI，配置外置，支持工具调用）。"
    )
    p.add_argument("--model", "-m", help="临时覆盖配置中的默认模型")
    p.add_argument("--config", "-c", default=CONFIG_NAME, help=f"配置文件路径（默认 {CONFIG_NAME}）")
    p.add_argument("--prompt", "-p", default=PROMPT_NAME, help=f"系统提示词文件路径（默认 {PROMPT_NAME}）")
    p.add_argument("--version", "-V", action="version", version=f"{APP} {VERSION}")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    # 管道输入时统一按 UTF-8 解码，避免非 ASCII 乱码（交互式终端由输入层负责）
    if not getattr(sys.stdin, "isatty", lambda: False)():
        try:
            sys.stdin.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    interactive = is_interactive()

    try:
        cfg, existed = read_config(args.config)
    except HarnessError as e:
        emit_error(str(e))
        return 1

    missing = config_problems(cfg)
    if missing:
        if not interactive:
            emit_error(config_error_text(args.config, missing))
            return 1
        if not existed:
            emit_info(f"已自动创建空配置 {args.config}，进入配置界面…")
        else:
            emit_info(f"配置不完整（{'、'.join(missing)} 为空），进入配置界面…")
        try:
            newcfg = edit_config(cfg)
        except HarnessError as e:
            emit_error(str(e))
            return 1
        if newcfg is None or config_problems(newcfg):
            emit_error("配置未完成，已退出。请填写 base_url / api_key / model 后重试。")
            return 1
        try:
            write_config(args.config, newcfg)
        except OSError as e:
            emit_error(f"保存配置失败：{e}")
            return 1
        cfg = newcfg

    try:
        system_prompt = load_system_prompt(args.prompt)
    except HarnessError as e:
        emit_error(str(e))
        return 1

    model = args.model.strip() if args.model else str(cfg.get("model") or "").strip()
    if not model:
        emit_error("模型未指定：请设置配置中的 model 或使用 --model。")
        return 1

    state = ModelState(cfg, model)
    if interactive:
        return TuiApp(cfg, state, system_prompt, args.config).run()
    return run_plain(cfg, state, system_prompt, args.config)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
