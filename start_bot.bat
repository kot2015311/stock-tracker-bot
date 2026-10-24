@echo off
chcp 65001 >nul
rem Запуск бота с автоперезапуском. Положите ярлык на этот файл в автозагрузку (Win+R -> shell:startup).
cd /d "%~dp0"
:loop
"%~dp0venv\Scripts\python.exe" main.py
echo Бот остановился, перезапуск через 30 секунд... (закройте окно, чтобы выключить)
timeout /t 30 /nobreak >nul
goto loop
