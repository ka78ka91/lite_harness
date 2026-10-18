# lite_narness

极简的 LLM 命令行对话工具（TUI）。配置外置、启动简单、够用即可。

## 语言与依赖

- **语言**：Python 3.8+（跨平台，Windows / macOS / Linux 均可）。
- **运行时依赖**：仅 [prompt_toolkit](https://python-prompt-toolkit.readthedocs.io/)（用于 TUI：Tab 补全、鼠标点击、配置界面）。
  其余（网络、JSON、文件）均使用 Python 标准库。
- **打包依赖**：仅打包时需要 [PyInstaller](https://pyinstaller.org/)（生成单文件 exe）。
- 依赖清单见 `requirements.txt`。

## 功能

- 交互式多轮对话（对话历史仅存于当前进程内存，退出即清空）。
- **Tab 补全**：命令（`/help`、`/model` …）与模型名可补全。
- **鼠标支持**：可点击补全项选中；配置界面中的选项/模型可用鼠标点选。
- **应用内配置**：`/config` 打开配置界面，可修改并保存到配置文件，无需手动编辑。
- **自动创建空配置**：配置缺失时自动生成空 `config.json` 并直接进入配置界面。
- 系统提示词外置为 `system_prompt.md`，启动时读取。

## 文件说明

| 文件 | 作用 |
| --- | --- |
| `lite_narness.py` | 入口实现（单文件） |
| `lite_narness.cmd` / `lite_narness.sh` | Windows / Unix 开发启动脚本 |
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
   | Windows 开发 | `lite_narness.cmd` |
   | macOS / Linux 开发 | `chmod +x lite_narness.sh && ./lite_narness.sh` |
   | 打包后的 exe | `lite_narness.exe` |

> 裸命令 `lite_narness`：把打包产物 `lite_narness.exe` 放到工作区目录并将其加入系统 `PATH` 即可。

## 配置说明（config.json）

```json
{
  "base_url": "https://api.example.com/v1",
  "api_key": "sk-REPLACE_WITH_YOUR_KEY",
  "model": "gpt-4o-mini",
  "models": ["gpt-4o-mini", "gpt-4o"],
  "temperature": 0.7,
  "timeout": 60
}
```

- `base_url`：API 服务地址（OpenAI 兼容）。会自动补 `/chat/completions`。
- `api_key`：API Key（**不要**提交到版本库）。
- `model`：默认模型名。
- `models`（可选）：可选模型列表，供补全与 `/models` 使用。
- `temperature` / `timeout`（可选）：采样温度与请求超时秒数。

配置与提示词均从**运行时当前目录**读取；修改后**重新启动**即生效（或在应用内用 `/config` 修改，即时生效）。

## 使用方式

启动后直接输入文本即可对话。输入 `/` 时会出现补全菜单：**Tab** 切换、**回车**确认，或**鼠标点击**选中。

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
lite_narness --model <名称>     临时覆盖默认模型
lite_narness --config <路径>    指定配置文件（默认 config.json）
lite_narness --prompt <路径>    指定系统提示词文件（默认 system_prompt.md）
lite_narness --version
```

## 打包为 exe

```
build_exe.cmd
```

或手动执行：

```
python -m pip install prompt_toolkit pyinstaller
python -m PyInstaller --onefile --name lite_narness --clean lite_narness.py
```

产物在 `dist\lite_narness.exe`，单文件、目标机无需 Python 环境。把它与 `config.json`、`system_prompt.md` 放在同一目录。

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
