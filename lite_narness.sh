#!/usr/bin/env bash
# 开发时的启动脚本（打包后直接用 ./lite_narness）
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$DIR/lite_narness.py" "$@"
