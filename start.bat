@echo off
chcp 65001 >nul
rem ============================================================
rem 图书管理系统 - Windows 一键启动脚本
rem 功能: 检查 Python 环境 -> 检查 Flask 依赖 -> 启动服务
rem ============================================================

rem 切换到脚本所在目录, 避免从其他路径启动时找不到文件
cd /d "%~dp0"

echo.
echo ============================================
echo   图书管理系统
echo ============================================
echo.

rem --- 检查 Python 是否可用 ---
python --version >nul 2>&1
if errorlevel 1 goto :no_python

rem --- 检查 Flask 是否已安装 ---
python -c "import flask" >nul 2>&1
if errorlevel 1 goto :no_flask

rem --- 依赖就绪, 启动应用 ---
echo [1/2] 环境检查通过, 正在启动...
echo.
echo 访问地址: http://127.0.0.1:5000
echo 管理员:   admin / admin123
echo 读者:     reader / reader123
echo.
echo 按 Ctrl+C 停止服务
echo.
python app.py
goto :end

rem --- 未检测到 Python ---
:no_python
echo [错误] 未检测到 Python。
echo.
echo 请先安装 Python 3.8 或更高版本:
echo   1. 前往 https://www.python.org/downloads/ 下载
echo   2. 安装时务必勾选 "Add Python to PATH"
echo   3. 安装完成后重新运行本脚本
echo.
pause
goto :end

rem --- 未安装 Flask ---
:no_flask
echo [1/2] 未检测到 Flask 依赖, 正在自动安装...
echo.
python -m pip install -r requirements.txt
if errorlevel 1 goto :install_failed
echo.
echo 依赖安装完成, 正在启动...
echo.
echo 访问地址: http://127.0.0.1:5000
echo.
python app.py
goto :end

rem --- 安装失败 ---
:install_failed
echo.
echo [错误] 依赖安装失败, 请检查网络后手动执行:
echo   pip install -r requirements.txt
echo.
pause

:end