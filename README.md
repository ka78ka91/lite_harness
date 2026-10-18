# lite_narness

极简的 LLM 命令行对话工具（TUI）。配置外置、启动简单、够用即可。

## 语言与依赖

- **语言**：Python 3.8+（跨平台，Windows / macOS / Linux 均可）。
- **运行时依赖**：仅 Python 标准库（网络使用 `urllib`，TUI 使用 ANSI 转义）。**零第三方依赖**。
- **打包依赖**：仅打包时需要 [PyInstaller](https://pyinstaller.org/)（用于生成单文件 exe）。

## 文件说明

| 文件 | 作用 |
| --- | --- |
| `lite_narness.py` | 入口实现（单文件） |
| `lite_narness.cmd` | Windows 开发启动脚本 |
| `lite_narness.sh` | macOS / Linux 开发启动脚本 |
| `config.json` | **你的**配置（需自行创建，勿提交版本库） |
| `config.example.json` | 配置模板（不含真实 Key） |
| `system_prompt.md` | 系统提示词（启动时读取） |
| `build_exe.cmd` | 一键打包 exe 的脚本 |

## 快速开始

1. 安装 Python 3.8+。
2. 复制模板并填写配置：
   ```
   copy config.example.json config.json      # Windows
   cp   config.example.json config.json      # macOS / Linux
   ```
   编辑 `config.json`，至少填好 `base_url`、`api_key`、`model`。
3. 按需编辑 `system_prompt.md`（缺失时会自动创建默认内容）。
4. 在工作区目录运行：

   **Windows**
   ```
   lite_narness.cmd
   ```
   **macOS / Linux**
   ```
   chmod +x lite_narness.sh
   ./lite_narness.sh
   ```
   **打包后的 exe**（把 `lite_narness.exe` 放到工作区目录）：
   ```
   lite_narness.exe
   ```

> 想让 `lite_narness` 直接可用：把打包产物 `lite_narness.exe`（或 `lite_narness.sh` 加入 PATH）放到工作区目录，或将其所在目录加入系统 `PATH`。

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
- `models`（可选）：可选模型列表，供 `/models` 命令展示。
- `temperature` / `timeout`（可选）：采样温度与请求超时秒数。

修改配置或 `system_prompt.md` 后，**重新启动**即可生效。

## 使用方式

启动后进入交互式多轮对话，直接输入文本即可。

- 临时切换模型：`lite_narness --model <模型名>`（或运行时输入 `/model <模型名>`）。
- 指定其它配置文件：`lite_narness --config other.json`。
- 指定其它提示词文件：`lite_narness --prompt other.md`。
- 退出：输入 `exit` 或 `quit`（也可 Ctrl+C / Ctrl+D）。

内置命令（`/help` 查看）：

```
/help            显示帮助
/model           显示当前模型
/model <名称>    临时切换模型（仅本次会话）
/models          列出配置中的可选模型
/clear           清空对话历史（保留系统提示词）
/prompt          显示当前系统提示词
exit | quit      退出
```

对话历史仅保存在当前进程内存中，退出即清空（保持极简）。

## 打包为 exe

```
build_exe.cmd
```

或手动执行：

```
python -m pip install pyinstaller
python -m PyInstaller --onefile --name lite_narness --clean lite_narness.py
```

产物在 `dist\lite_narness.exe`，单文件、无需 Python 环境。把它与 `config.json`、`system_prompt.md` 放在同一目录。

## 安全注意事项

- **API Key 只存在于 `config.json`**，代码中不硬编码任何真实 URL、Key 或模型名。
- Key **不会**被打印到终端、日志或错误信息中；错误提示仅展示状态码与简要原因。
- 请将 `config.json` 加入 `.gitignore`，**不要**提交真实配置到版本库。
- `config.example.json` 只含占位符，可安全提交。

## 错误与提示

- 配置缺失/为空时：给出明确原因与最小修复说明，不会退出泄漏敏感信息。
- 网络错误 / 超时 / 响应格式异常：给出可理解的中文提示。
- API 返回非成功状态：显示 `HTTP 状态码 + 简要原因`，不泄露 Key。
