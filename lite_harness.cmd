@echo off
setlocal
set "DIR=%~dp0"
if exist "%DIR%lite_harness.exe" (
  "%DIR%lite_harness.exe" %*
) else (
  python "%DIR%lite_harness.py" %*
)
