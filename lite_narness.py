#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
lite_narness — 极简 LLM 命令行对话工具 (TUI)

设计目标：配置外置、启动简单、够用即可。

依赖：prompt_toolkit（TUI：Tab 补全 / 鼠标点击 / 配置界面）。
      网络与文件均使用 Python 标准库。

用法：
    lite_narness                  使用 config.json 中的默认模型
    lite_narness --model <名称>   临时覆盖默认模型
    lite_narness --config <路径>  指定配置文件
    lite_narness --prompt <路径>  指定系统提示词文件

配置与提示词均在“当前工作目录”读取；缺失时自动创建空配置并进入配置界面。
API Key 不会被打印到终端。
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import threading
import time
import unicodedata
import urllib.error
import urllib.request

APP = "lite_narness"
VERSION = "2.0.0"
CONFIG_NAME = "config.json"
CONFIG_EXAMPLE = "config.example.json"
PROMPT_NAME = "system_prompt.md"

REQUIRED_KEYS = ("base_url", "api_key", "model")
CONFIG_KEYS = ("base_url", "api_key", "model", "models", "temperature", "timeout")

EMPTY_CONFIG = {
    "base_url": "",
    "api_key": "",
    "model": "",
    "models": [],
    "temperature": 0.7,
    "timeout": 60,
}

DEFAULT_SYSTEM_PROMPT = """你是一个简洁、准确、乐于助人的 AI 助手。

- 使用与用户相同的语言回答。
- 回答直接、切题，避免空话与冗余。
- 涉及代码时给出可运行的示例，并简要说明。
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

HELP_TEXT = """可用命令（也可直接输入普通文本对话；Tab 补全，可用鼠标点击补全项）：
  /help            显示本帮助
  /model           显示当前模型
  /model <名称>    临时切换模型（仅本次会话）
  /models          列出配置中的可选模型
  /clear           清空对话历史（保留系统提示词）
  /prompt          显示当前系统提示词
  /config          打开配置界面，可修改并保存到配置文件
  exit | quit      退出（也可按 Ctrl+C / Ctrl+D）"""

# --------------------------------------------------------------------------- #
# 可选依赖 prompt_toolkit
# --------------------------------------------------------------------------- #
try:
    from prompt_toolkit import PromptSession, print_formatted_text
    from prompt_toolkit.application import Application
    from prompt_toolkit.application.current import get_app
    from prompt_toolkit.completion import Completer, Completion
    from prompt_toolkit.formatted_text import FormattedText
    from prompt_toolkit.key_binding.defaults import load_key_bindings
    from prompt_toolkit.key_binding.key_bindings import KeyBindings, merge_key_bindings
    from prompt_toolkit.layout import Layout
    from prompt_toolkit.layout.containers import HSplit
    from prompt_toolkit.shortcuts import (
        CompleteStyle,
        message_dialog,
        radiolist_dialog,
    )
    from prompt_toolkit.styles import Style
    from prompt_toolkit.widgets import Button, Dialog, Label, TextArea

    HAVE_PT = True
    STYLE = Style.from_dict(
        {
            "prompt": "#00af00 bold",
            "user": "#00af00 bold",
            "ai": "#268bd2 bold",
            "sys": "#6c71c4 bold",
            "bar": "#888888",
            "dim": "#888888",
            "error": "#e01b24 bold",
            "banner": "#c678dd bold",
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


def _display_width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)


def _pad(text: str, width: int) -> str:
    return text + " " * max(0, width - _display_width(text))


def emit_error(message: str) -> None:
    sys.stdout.write(_ansi("31", "✗ ") + _ansi("31", message) + "\n")
    sys.stdout.flush()


def emit_info(message: str) -> None:
    sys.stdout.write(_ansi("90", "• " + message) + "\n")
    sys.stdout.flush()


# --------------------------------------------------------------------------- #
# 配置读写
# --------------------------------------------------------------------------- #
def write_config(path: str, cfg: dict) -> None:
    data = {k: cfg.get(k, EMPTY_CONFIG[k]) for k in CONFIG_KEYS}
    for k, v in cfg.items():  # 保留用户自定义的额外字段
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
        detail = ""
        try:
            body = e.read().decode("utf-8", errors="replace")
            obj = json.loads(body)
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
        content = obj["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise HarnessError("响应结构异常：未找到 choices[0].message.content。")
    return "" if content is None else str(content)


# --------------------------------------------------------------------------- #
# UI：纯文本 / TUI
# --------------------------------------------------------------------------- #
class BaseUI:
    def thinking_start(self) -> None:
        self._stop = threading.Event()
        self._spin = threading.Thread(target=self._spin_loop, daemon=True)
        self._spin.start()

    def _spin_loop(self) -> None:
        frames = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
        i = 0
        while not self._stop.is_set():
            sys.stdout.write("\r\033[K" + _ansi("90", f"{frames[i % len(frames)]} 思考中…"))
            sys.stdout.flush()
            i += 1
            time.sleep(0.08)

    def thinking_stop(self) -> None:
        self._stop.set()
        self._spin.join()
        sys.stdout.write("\r\033[K")
        sys.stdout.flush()

    def print_info(self, message: str) -> None:
        emit_info(message)

    def print_error(self, message: str) -> None:
        emit_error(message)

    def print_block(self, prefix: str, text: str, kind: str) -> None:
        raise NotImplementedError

    def print_banner(self, model: str, endpoint: str, prompt_len: int) -> None:
        title = f" {APP} v{VERSION}  ·  极简 LLM 对话"
        hint = "  Tab 补全 · 鼠标可点选 · 输入 /help 查看命令"
        width = 56
        print()
        print(_ansi("35", "╭" + "─" * width + "╮"))
        print(_ansi("35", "│") + _pad(title, width) + _ansi("35", "│"))
        print(_ansi("35", "│") + _ansi("90", _pad(hint, width)) + _ansi("35", "│"))
        print(_ansi("35", "╰" + "─" * width + "╯"))
        emit_info(f"模型: {model}")
        emit_info(f"接口: {endpoint}")
        emit_info(f"系统提示词: {prompt_len} 字符")
        print()

    def ask(self) -> str:
        raise NotImplementedError


class PlainUI(BaseUI):
    """非交互环境下的降级实现（无补全 / 无鼠标）。"""

    def print_block(self, prefix: str, text: str, kind: str) -> None:
        codes = {"user": "32", "ai": "34", "sys": "36"}.get(kind, "0")
        lines = (text if text else "(空回复)").splitlines() or [""]
        bar = _ansi("90", "│")
        for idx, line in enumerate(lines):
            head = _ansi(codes, prefix) if idx == 0 else " " * len(prefix)
            sys.stdout.write(f"{bar} {head} {line}\n")
        sys.stdout.flush()

    def ask(self) -> str:
        return input(_ansi("1;32", "你 › "))


class TuiUI(BaseUI):
    """prompt_toolkit TUI：Tab 补全 + 鼠标点击选择补全项。"""

    def __init__(self, completer):
        self.session = PromptSession(
            completer=completer,
            complete_while_typing=True,
            mouse_support=True,
            complete_style=CompleteStyle.COLUMN,
            style=STYLE,
        )

    def print_block(self, prefix: str, text: str, kind: str) -> None:
        lines = (text if text else "(空回复)").splitlines() or [""]
        frags = []
        for idx, line in enumerate(lines):
            head = prefix if idx == 0 else " " * len(prefix)
            frags.append(("class:bar", "│ "))
            frags.append((f"class:{kind}", head))
            frags.append(("", " " + line + "\n"))
        print_formatted_text(FormattedText(frags), style=STYLE)

    def print_info(self, message: str) -> None:
        print_formatted_text(FormattedText([("class:dim", "• " + message)]), style=STYLE)

    def print_error(self, message: str) -> None:
        print_formatted_text(
            FormattedText([("class:error", "✗ " + message)]), style=STYLE
        )

    def print_banner(self, model: str, endpoint: str, prompt_len: int) -> None:
        title = f" {APP} v{VERSION}  ·  极简 LLM 对话"
        hint = "  Tab 补全 · 鼠标可点选 · 输入 /help 查看命令"
        width = 56
        print_formatted_text(
            FormattedText(
                [
                    ("class:banner", "╭" + "─" * width + "╮\n"),
                    ("class:banner", "│"),
                    ("class:banner", _pad(title, width)),
                    ("class:banner", "│\n"),
                    ("class:banner", "│"),
                    ("class:dim", _pad(hint, width)),
                    ("class:banner", "│\n"),
                    ("class:banner", "╰" + "─" * width + "╯\n"),
                    ("class:dim", f"• 模型: {model}\n"),
                    ("class:dim", f"• 接口: {endpoint}\n"),
                    ("class:dim", f"• 系统提示词: {prompt_len} 字符\n"),
                ]
            ),
            style=STYLE,
        )

    def ask(self) -> str:
        return self.session.prompt([("class:prompt", "你 › ")])


# --------------------------------------------------------------------------- #
# 补全
# --------------------------------------------------------------------------- #
class ModelState:
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

    def text_dialog(title: str, text: str = "", default: str = "", password: bool = False) -> "Application":
        """单行文本输入对话框：回车即确认（也可鼠标点击“确定”）。"""
        def on_accept(buf) -> bool:
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


# --------------------------------------------------------------------------- #
# 配置界面（鼠标可点击）
# --------------------------------------------------------------------------- #
def _masked_key(cfg: dict) -> str:
    return "••••••••（已设置）" if str(cfg.get("api_key") or "").strip() else "（空）"


def _config_values(cfg: dict) -> list:
    return [
        ("base_url", f"base_url      {cfg.get('base_url') or '（空）'}"),
        ("api_key", f"api_key       {_masked_key(cfg)}"),
        ("model", f"model         {cfg.get('model') or '（空）'}"),
        ("models", f"models        {', '.join(cfg.get('models') or []) or '（空）'}"),
        ("temperature", f"temperature   {cfg.get('temperature')}"),
        ("timeout", f"timeout       {cfg.get('timeout')}"),
    ]


def edit_config(cfg: dict) -> dict | None:
    """打开配置界面。返回修改后的配置；取消则返回 None。"""
    if not HAVE_PT:
        raise HarnessError(
            "当前环境不支持图形配置界面（缺少 prompt_toolkit 或非交互终端）。\n"
            "  修复：手动编辑配置文件，或安装 prompt_toolkit 后在交互终端运行。"
        )
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
                    title="配置不完整",
                    text="请先填写：" + "、".join(missing),
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
                        title="model",
                        text="输入模型名称",
                        default=str(cfg.get("model") or ""),
                    ).run()
                    if t is not None:
                        cfg["model"] = t.strip()
                elif v is not None:
                    cfg["model"] = v
            else:
                t = text_dialog(
                    title="model",
                    text="输入模型名称",
                    default=str(cfg.get("model") or ""),
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
                    x.strip()
                    for x in t.replace("，", ",").split(",")
                    if x.strip()
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
                        message_dialog(
                            title="无效输入", text="temperature 必须是数字。"
                        ).run()
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
                    message_dialog(
                        title="无效输入", text="timeout 必须是整数。"
                    ).run()


# --------------------------------------------------------------------------- #
# 对话主循环
# --------------------------------------------------------------------------- #
def is_interactive() -> bool:
    return (
        HAVE_PT
        and bool(getattr(sys.stdin, "isatty", lambda: False)())
        and bool(getattr(sys.stdout, "isatty", lambda: False)())
    )


def run_chat(cfg: dict, model: str, system_prompt: str, config_path: str, interactive: bool) -> int:
    state = ModelState(cfg, model)
    ui: BaseUI = TuiUI(CommandCompleter(state)) if interactive else PlainUI()

    history = []
    if system_prompt:
        history.append({"role": "system", "content": system_prompt})

    ui.print_banner(state.model, build_endpoint(cfg["base_url"]), len(system_prompt))

    while True:
        try:
            line = ui.ask()
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
                ui.print_block("help", HELP_TEXT, "sys")
            elif cmd == "/model":
                if arg:
                    state.model = arg
                    ui.print_info(f"已切换模型: {state.model}")
                else:
                    ui.print_info(f"当前模型: {state.model}")
            elif cmd == "/models":
                names = [m for m in (cfg.get("models") or []) if m]
                if names:
                    for m in names:
                        ui.print_info(m + ("  ← 当前" if m == state.model else ""))
                else:
                    ui.print_info("配置中未提供 models 列表。")
            elif cmd == "/clear":
                history = [h for h in history if h["role"] == "system"]
                ui.print_info("对话历史已清空。")
            elif cmd == "/prompt":
                ui.print_block("sys", system_prompt or "（空）", "sys")
            elif cmd == "/config":
                try:
                    newcfg = edit_config(cfg)
                except HarnessError as e:
                    ui.print_error(str(e))
                    continue
                if newcfg:
                    try:
                        write_config(config_path, newcfg)
                    except OSError as e:
                        ui.print_error(f"保存配置失败：{e}")
                        continue
                    cfg.clear()
                    cfg.update(newcfg)
                    state.cfg = cfg
                    if str(newcfg.get("model") or "").strip():
                        state.model = newcfg["model"].strip()
                    ui.print_info("配置已保存并生效。")
                else:
                    ui.print_info("已取消，配置未修改。")
            elif cmd in ("/exit", "/quit"):
                break
            else:
                ui.print_error(f"未知命令：{cmd}（输入 /help 查看）")
            continue

        history.append({"role": "user", "content": text})
        ui.thinking_start()
        try:
            reply = call_api(cfg, state.model, history)
        except HarnessError as e:
            ui.thinking_stop()
            ui.print_error(str(e))
            history.pop()
            continue
        except Exception as e:  # 兜底，不泄露内部细节
            ui.thinking_stop()
            ui.print_error(f"发生未预期错误：{type(e).__name__}")
            history.pop()
            continue
        ui.thinking_stop()

        history.append({"role": "assistant", "content": reply})
        ui.print_block("AI › ", reply, "ai")

    ui.print_info("再见。")
    return 0


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #
def parse_args(argv) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog=APP, description="极简 LLM 命令行对话工具（TUI，配置外置）。"
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

    return run_chat(cfg, model, system_prompt, args.config, interactive)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
