@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PY=python"
where py >nul 2>nul
if %errorlevel%==0 set "PY=py -3"
if exist ".venv\Scripts\python.exe" goto deps
echo 正在创建运行环境 .venv …… / Creating the .venv environment...
%PY% -m venv .venv
if not errorlevel 1 goto deps
echo 创建失败：请先安装 Python 3（https://www.python.org/）。
echo Failed: install Python 3 first from https://www.python.org/
goto end
:deps
echo 正在安装依赖…… / Installing dependencies...
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt
if not errorlevel 1 goto ok
echo 官方源下载失败，改用清华镜像重试…… / PyPI failed, retrying with the Tsinghua mirror...
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
if not errorlevel 1 goto ok
echo 依赖安装失败，请检查网络后重试。 / Installing dependencies failed; check your network and try again.
goto end
:ok
echo 安装完成。 / Done.
:end
if /i not "%~1"=="nopause" pause
