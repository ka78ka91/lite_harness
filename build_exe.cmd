@echo off
setlocal
echo [1/2] 安装打包工具 PyInstaller（如已安装会自动跳过）...
python -m pip install --upgrade pyinstaller || goto :err

echo [2/2] 打包单文件可执行程序 lite_narness.exe ...
python -m PyInstaller --onefile --name lite_narness --clean lite_narness.py || goto :err

echo.
echo 完成：dist\lite_narness.exe
echo 将该 exe 复制到你的工作区目录，和 config.json、system_prompt.md 放在一起即可运行。
goto :eof

:err
echo.
echo 打包失败，请检查上面的错误信息。
exit /b 1
