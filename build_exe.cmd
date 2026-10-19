@echo off
setlocal
echo [1/2] 安装依赖（prompt_toolkit 与打包工具 PyInstaller）...
python -m pip install --upgrade prompt_toolkit pyinstaller || goto :err

echo [2/2] 打包单文件可执行程序 lite_harness.exe ...
python -m PyInstaller --onefile --name lite_harness --clean --noconfirm lite_harness.py || goto :err

echo.
echo 完成：dist\lite_harness.exe
echo 将该 exe 复制到你的工作区目录，和 config.json、system_prompt.md 放在一起即可运行。
goto :eof

:err
echo.
echo 打包失败，请检查上面的错误信息。
exit /b 1
