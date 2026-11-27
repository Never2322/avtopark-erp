@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Контур

echo.
echo  Останавливаю программу...
docker compose down
echo.
echo  Остановлено. Данные и резервные копии сохранены.
echo  Чтобы включить снова, запустите файл Запустить.bat.
echo.
pause
