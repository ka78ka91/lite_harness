# lite_harness

极简的 LLM 命令行对话工具（全屏 TUI）。配置外置、启动简单、够用即可；支持流式输出与工具调用（function calling）。

## 语言与依赖

- **语言**：Python 3.8+（跨平台，Windows / macOS / Linux 均可）。
- **运行时依赖**：仅 [prompt_toolkit](https://python-prompt-toolkit.readthedocs.io/)（用于 TUI：Tab 补全、鼠标点击、配置界面）。
  其余（网络、JSON、文件）均使用 Python 标准库。
- **打包依赖**：仅打包时需要 [PyInstaller](https://pyinstaller.org/)（生成单文件 exe）。
- 依赖清单见 `requirements.txt`。

## 功能

- **全屏界面**：左侧对话区、右侧 `Context` 侧栏、底部状态栏。
- **流式输出**：回复边生成边显示（SSE）；可随时 `Esc` 中断当前生成。
- **工具调用（function calling）**：内置工具，模型可自动调用并继续作答：
  - `run_shell`：执行 shell 命令并返回输出。
  - `read_file`：读取本地文件。
  - `write_file`：写入本地文件。
- 交互式多轮对话（对话历史仅存于当前进程内存，退出即清空）。
- **Tab 补全**：命令（`/help`、`/model` …）与模型名可补全。
- **鼠标支持**：可点击补全项选中；配置界面中的选项/模型可用鼠标点选。
- **应用内配置**：`/config` 打开配置界面，可修改并保存到配置文件，无需手动编辑。
- **自动创建空配置**：配置缺失时自动生成空 `config.json` 并直接进入配置界面。
- 系统提示词外置为 `system_prompt.md`，启动时读取。

## 文件说明

| 文件 | 作用 |
| --- | --- |
| `lite_harness.py` | 入口实现（单文件） |
| `lite_harness.cmd` / `lite_harness.sh` | Windows / Unix 开发启动脚本 |
| `config.json` | **你的**配置（缺失时自动创建，勿提交版本库） |
| `config.example.json` | 配置模板（不含真实 Key） |
| `system_prompt.md` | 系统提示词（缺失时自动创建默认内容） |
| `requirements.txt` | 运行时依赖 |
| `build_exe.cmd` | 一键打包 exe |

## 快速开始

1. 安装 Python 3.8+ 与依赖：
   ```
   python -m pip install -r requirements.txt
   ```
2. 复制模板并填写配置（也可跳过：首次启动会自动创建空配置并进入配置界面）：
   ```
   copy config.example.json config.json      # Windows
   cp   config.example.json config.json      # macOS / Linux
   ```
3. 在工作区目录运行：

   | 环境 | 命令 |
   | --- | --- |
   | Windows 开发 | `lite_harness.cmd` |
   | macOS / Linux 开发 | `chmod +x lite_harness.sh && ./lite_harness.sh` |
   | 打包后的 exe | `lite_harness.exe` |

> 裸命令 `lite_harness`：把打包产物 `lite_harness.exe` 放到工作区目录并将其加入系统 `PATH` 即可。

## 配置说明（config.json）

```json
{
  "base_url": "https://api.example.com/v1",
  "api_key": "sk-REPLACE_WITH_YOUR_KEY",
  "model": "gpt-4o-mini",
  "models": ["gpt-4o-mini", "gpt-4o"],
  "temperature": 0.7,
  "timeout": 60,
  "context_window": 128000,
  "include_usage": false
}
```

- `base_url`：API 服务地址（OpenAI 兼容）。会自动补 `/chat/completions`。
- `api_key`：API Key（**不要**提交到版本库）。
- `model`：默认模型名。
- `models`（可选）：可选模型列表，供补全与 `/models` 使用。
- `temperature` / `timeout`（可选）：采样温度与请求超时秒数。
- `context_window`（可选）：上下文窗口大小（用于状态栏占用估算），默认 128000。
- `include_usage`（可选）：是否要求服务端在流式响应中返回用量统计（`stream_options`）。部分服务不支持，默认 `false`（关闭时按字符数估算）。

配置与提示词均从**运行时当前目录**读取；修改后**重新启动**即生效（或在应用内用 `/config` 修改，即时生效）。

## 使用方式

启动后直接输入文本即可对话。输入 `/` 时会出现补全菜单：**Tab** 切换、**回车**确认，或**鼠标点击**选中。生成过程中按 **Esc** 可中断。

内置命令（`/help` 查看）：

```
/help            显示帮助
/model           显示当前模型
/model <名称>    临时切换模型（仅本次会话）
/models          列出配置中的可选模型
/clear           清空对话历史（保留系统提示词）
/prompt          显示当前系统提示词
/config          打开配置界面，可修改并保存
exit | quit      退出（也可 Ctrl+C / Ctrl+D）
```

启动参数：

```
lite_harness --model <名称>     临时覆盖默认模型
lite_harness --config <路径>    指定配置文件（默认 config.json）
lite_harness --prompt <路径>    指定系统提示词文件（默认 system_prompt.md）
lite_harness --version
lite_harness --serve --port 8899 --key sk-lite-test   # 内置模拟 AI 服务（见下）
```

## 自测：内置模拟 AI 服务（`--serve`）

无需真实 API Key，本程序可以**自己变成一个 AI**：启动一个 OpenAI 兼容的本地 HTTP 接口，用 Key 鉴权，专门用来测试本程序（流式、工具调用、错误处理等）。

```
lite_harness --serve --port 8899 --key sk-lite-test
```

启动后会打印可直接使用的配置。再用另一个终端（或把 `config.json`）指向它即可：

```json
{
  "base_url": "http://127.0.0.1:8899/v1",
  "api_key": "sk-lite-test",
  "model": "mock-echo"
}
```

接口：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/v1/models` | 列出模拟模型（需鉴权） |
| GET | `/health` | 健康检查 |
| POST | `/v1/chat/completions` | 对话补全，支持 `stream` 与 `tools` |

模拟行为（可预测，便于断言）：

- 普通消息 → 回显 `（模拟 AI · <model>）…你说：<内容>`。
- 消息为 `工具: <名称> <JSON参数>`（且请求带 `tools`）→ 返回对应的 `tool_calls`。
- 收到工具结果后 → 返回一段收尾文本，验证工具调用闭环。
- 消息以 `!error` 开头 → 返回 HTTP 500，用于测试错误分支。

参数：`--host`（默认 `127.0.0.1`）、`--port`（默认 `8899`）、`--key`（默认 `sk-lite-test`）。

## 工具调用（function calling）

模型在需要时会自动调用内置工具（`run_shell` / `read_file` / `write_file`），执行结果会回填并继续生成最终回答。工具在对话区以 `⚙ 工具名 {参数}` 及结果缩略形式展示。

> 安全须知：工具**全自动执行、不做路径限制**，等同于把本机 shell 与文件系统交给模型。请仅在可信环境与可信模型下使用；不要让它处理不可信的输入。

## 打包为 exe

```
build_exe.cmd
```

或手动执行：

```
python -m pip install prompt_toolkit pyinstaller
python -m PyInstaller --onefile --name lite_harness --clean lite_harness.py
```

产物在 `dist\lite_harness.exe`，单文件、目标机无需 Python 环境。把它与 `config.json`、`system_prompt.md` 放在同一目录。

> 提示：TUI（补全菜单、配置窗口）需要真实终端窗口；若在无终端环境（如管道重定向）运行，会自动降级为纯文本模式。

## 安全注意事项

- **API Key 只存在于 `config.json`**，代码中不硬编码任何真实 URL、Key 或模型名。
- Key **不会**被打印到终端、日志或错误信息中；配置界面里以 `••••••••` 掩码显示。
- 请将 `config.json` 加入 `.gitignore`，**不要**提交真实配置到版本库。
- `config.example.json` 只含占位符，可安全提交。

## 错误与提示

- 配置缺失/为空：自动创建模板并进入配置界面；非交互环境则给出明确原因与最小修复说明。
- 网络错误 / 超时 / 响应格式异常：给出可理解的中文提示。
- API 返回非成功状态：显示 `HTTP 状态码 + 简要原因`，不泄露 Key。
