@echo off
chcp 65001 >nul
cd /d "%~dp0"
if exist ".venv\Scripts\pythonw.exe" goto run
echo 第一次使用，正在安装运行环境（约 1 分钟）……
echo First run: setting up the Python environment, about a minute...
call "%~dp0install.bat" nopause
if exist ".venv\Scripts\pythonw.exe" goto run
echo 安装失败：请检查网络和 Python 是否装好，或把上面的报错发到项目的 Issue。
echo Setup failed: check your network and Python installation, or post the error above as an issue.
pause
exit /b 1
:run
start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0guard.pyw"
