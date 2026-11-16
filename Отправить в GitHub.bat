@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Отправка в GitHub

where git >nul 2>nul
if errorlevel 1 (
  echo.
  echo  Git не найден. Установите Git для Windows и запустите файл снова.
  pause
  exit /b 1
)

for /f "delims=" %%i in ('git config user.name') do set GIT_NAME=%%i
for /f "delims=" %%i in ('git config user.email') do set GIT_MAIL=%%i
if not defined GIT_NAME (
  echo.
  echo  В Git не указано имя автора. Выполните один раз в этой папке:
  echo    git config user.name "Ваше имя"
  echo    git config user.email "ваша@почта"
  pause
  exit /b 1
)
if not defined GIT_MAIL (
  echo.
  echo  В Git не указана почта автора. Выполните один раз в этой папке:
  echo    git config user.email "ваша@почта"
  pause
  exit /b 1
)

echo.
echo  Автор коммита: %GIT_NAME% ^<%GIT_MAIL%^>
echo.
git add -A
git status --short
echo.
set /p MSG= Описание изменений (Enter, чтобы взять «Статусы техники, тарифы и часы, табель, выход на работу, журнал заправок, отчёт за день, светлая и тёмная тема»): 
if "%MSG%"=="" set MSG=Статусы техники, тарифы и часы, табель, выход на работу, журнал заправок, отчёт за день, светлая и тёмная тема

git commit -m "%MSG%"
if errorlevel 1 (
  echo.
  echo  Коммит не создан: возможно, нечего отправлять или Git вернул ошибку.
  pause
  exit /b 1
)

git push
if errorlevel 1 (
  echo.
  echo  Отправка не удалась. Проверьте вход в GitHub и интернет, затем запустите файл снова.
  pause
  exit /b 1
)

echo.
echo  Готово. Изменения отправлены в репозиторий.
pause
