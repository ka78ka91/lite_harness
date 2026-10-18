@echo off
setlocal
set "DIR=%~dp0"
if exist "%DIR%lite_narness.exe" (
  "%DIR%lite_narness.exe" %*
) else (
  python "%DIR%lite_narness.py" %*
)
