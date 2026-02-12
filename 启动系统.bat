@echo off
chcp 65001 >nul
cls
echo ========================================
echo    矿机管理系统启动脚本
echo ========================================
echo.
echo 正在启动服务...
echo.
echo 启动后请访问: http://localhost:8000
echo.
echo 按 Ctrl+C 可停止服务
echo ========================================
echo.

cd /d "%~dp0"
python app.py

pause
