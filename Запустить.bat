@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Контур

echo.
echo  ===== Контур: запуск =====

where docker >nul 2>&1
if errorlevel 1 (
  echo.
  echo  Docker не установлен. Он нужен один раз.
  echo  Сейчас откроется страница загрузки. Поставьте Docker Desktop,
  echo  перезагрузите компьютер и запустите этот файл снова.
  start "" "https://www.docker.com/products/docker-desktop/"
  pause
  exit /b 1
)

docker info >nul 2>&1
if errorlevel 1 (
  echo.
  echo  Docker установлен, но не запущен.
  echo  Откройте Docker Desktop, подождите, пока он полностью загрузится,
  echo  и запустите этот файл снова.
  pause
  exit /b 1
)

set NEWENV=0
if not exist ".env" (
  powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\make-env.ps1"
  if errorlevel 1 (
    echo.
    echo  Не удалось создать файл настроек .env
    pause
    exit /b 1
  )
  set NEWENV=1
)

echo.
echo  Собираю и запускаю. В первый раз это занимает несколько минут.
docker compose up -d --build
if errorlevel 1 (
  echo.
  echo  Не получилось запустить. Сфотографируйте или скопируйте ошибку выше.
  pause
  exit /b 1
)

echo.
echo  Жду, пока программа включится...
powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\wait-ready.ps1"
if errorlevel 1 (
  echo.
  echo  Программа не ответила за 4 минуты.
  echo  Посмотрите, что случилось, командой:  docker compose logs backend
  pause
  exit /b 1
)

set HTTP_PORT=8080
for /f "tokens=1,* delims==" %%a in ('findstr /b "ADMIN_LOGIN= ADMIN_PASSWORD= HTTP_PORT=" .env') do set "%%a=%%b"

echo.
echo  ===== Готово =====
echo  Адрес:  http://localhost:%HTTP_PORT%
if "%NEWENV%"=="1" (
  echo.
  echo  Первый вход, запишите:
  echo     логин:   %ADMIN_LOGIN%
  echo     пароль:  %ADMIN_PASSWORD%
  echo  Пароль также лежит в файле .env. Смените его в разделе Профиль.
)
echo.
echo  Выключить программу: файл Остановить.bat. Данные при этом сохраняются.
start "" "http://localhost:%HTTP_PORT%"
echo.
pause
