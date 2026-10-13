@echo off
chcp 65001 >nul
rem ============================================================
rem 图书管理系统 - 一键启动脚本
rem 功能: 检查 Python 环境 -> 检查/自动安装 Flask -> 启动服务
rem       -> 延迟自动打开浏览器
rem 本窗口不要关闭, 关闭即停止服务
rem ============================================================

rem 切换到本脚本所在目录, 保证从任何位置双击都能正常找到文件
cd /d "%~dp0"

rem 源码放在 03-项目源码 子目录中, 进入后才能执行 python app.py
set "SRCDIR=03-项目源码"
if not exist "%SRCDIR%\app.py" goto :src_missing

rem 进入源码目录, 后续所有命令都在这里执行
cd /d "%SRCDIR%"

echo.
echo  ====================================================
echo       图书管理系统  -  正在启动
echo  ====================================================
echo.

rem --- 检查 Python 是否可用 ---
python --version >nul 2>&1
if errorlevel 1 goto :no_python

rem --- 检查 Flask 是否已安装 ---
python -c "import flask" >nul 2>&1
if errorlevel 1 goto :no_flask

echo  [1/3] 环境检查通过
echo  [2/3] 正在启动服务...
echo.
echo  ----------------------------------------------------
echo   管理员账号:  admin / admin123
echo   读者账号:    reader / reader123
echo  ----------------------------------------------------
echo   浏览器约 5 秒后自动打开 http://127.0.0.1:5000
echo   若页面暂时打不开, 等几秒按 F5 刷新即可
echo  ----------------------------------------------------
echo.

rem 用独立小窗口延迟约 5 秒后打开浏览器, 打开后该小窗口自动关闭。
rem 之所以不直接 start: 服务尚未就绪时会连接失败。
start "auto-open-browser" cmd /c "ping -n 6 127.0.0.1 >nul && start http://127.0.0.1:5000"

rem 在当前窗口启动服务。关闭本窗口或按 Ctrl+C 即停止服务。
python app.py
goto :end

rem --- 源码目录缺失(文件夹被改名或移动) ---
:src_missing
echo.
echo  [错误] 找不到源码目录 "%SRCDIR%"。
echo.
echo  请确认本文件与 "03-项目源码" 文件夹放在同一层目录下,
echo  且不要单独复制本 .bat 文件到别处运行。
echo.
pause
goto :end

rem --- 未检测到 Python ---
:no_python
echo.
echo  [错误] 没有检测到 Python 环境。
echo.
echo  请先安装 Python, 推荐直接用本文件夹里自带的安装包:
echo    "04-安装包\python-3.11.9-amd64.exe"  双击安装
echo    安装时务必勾选 "Add Python to PATH"  ^<-- 这一步最关键
echo  (也可到 https://www.python.org/downloads/ 下载)
echo.
if exist "%~dp004-安装包\python-3.11.9-amd64.exe" (
    echo  正在帮你打开安装程序, 装完后重新双击本文件即可。
    start "" "%~dp004-安装包\python-3.11.9-amd64.exe"
)
echo.
pause
goto :end

rem --- 未安装 Flask ---
:no_flask
echo  [1/3] 环境检查通过
echo  [2/3] 未检测到依赖 Flask, 正在自动安装(优先离线包)...
echo.
if exist "%~dp004-安装包\wheels" (
    python -m pip install -r requirements.txt --no-index --find-links="%~dp004-安装包\wheels"
) else (
    python -m pip install -r requirements.txt
)
if errorlevel 1 goto :install_failed
echo.
echo  依赖安装完成, 正在启动服务...
echo.
echo  ----------------------------------------------------
echo   管理员账号:  admin / admin123
echo   读者账号:    reader / reader123
echo  ----------------------------------------------------
echo   浏览器约 5 秒后自动打开 http://127.0.0.1:5000
echo  ----------------------------------------------------
echo.
start "auto-open-browser" cmd /c "ping -n 6 127.0.0.1 >nul && start http://127.0.0.1:5000"
python app.py
goto :end

rem --- 依赖安装失败 ---
:install_failed
echo.
echo  [错误] 依赖自动安装失败, 可能是网络原因。
echo  请手动在 "03-项目源码" 目录内执行下面这条命令后, 重新双击本文件:
echo.
echo      pip install -r requirements.txt
echo.
echo  如果下载很慢, 可换用国内镜像:
echo.
echo      pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
echo.
pause

:end