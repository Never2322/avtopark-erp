@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Перенос данных из старой программы

echo.
echo  ===== Перенос данных из старой программы =====
echo.
echo  Старая программа и её файл базы не меняются.
echo  Сначала идёт пробный запуск: ничего не записывается, только показывается, что получится.
echo  Записать в новую программу или нет, вы решите после просмотра.

where docker >nul 2>&1
if errorlevel 1 goto :nodocker
docker info >nul 2>&1
if errorlevel 1 goto :nodockerrun
docker compose exec -T backend python -c "print(1)" >nul 2>&1
if errorlevel 1 goto :notrunning

set "OLD=%~1"
if not "%OLD%"=="" goto :havefile
echo.
echo  Укажите файл базы старой программы (у неё это файл с расширением .db).
echo  Его можно перетащить мышкой в это окно, потом нажмите Enter.
set /p OLD= Файл: 
:havefile
set "OLD=%OLD:"=%"
if not exist "%OLD%" goto :nofile

echo.
echo  Копирую файл в программу...
docker compose exec -T backend sh -c "rm -rf /tmp/old && mkdir -p /tmp/old"
docker compose cp "%OLD%" backend:/tmp/old/old.db
if errorlevel 1 goto :copyfail
if exist "%OLD%-wal" docker compose cp "%OLD%-wal" backend:/tmp/old/old.db-wal
if exist "%OLD%-shm" docker compose cp "%OLD%-shm" backend:/tmp/old/old.db-shm

echo.
echo  ===== Пробный запуск =====
docker compose exec -T backend python -m app.cli import-old --db /tmp/old/old.db --report /tmp/old/report.txt
if errorlevel 1 goto :previewfail
docker compose cp backend:/tmp/old/report.txt "Отчёт о переносе.txt" >nul 2>&1
if exist "Отчёт о переносе.txt" (
  echo.
  echo  Полный отчёт сохранён в файл «Отчёт о переносе.txt» рядом с этим файлом. Сейчас он откроется.
  start "" notepad "Отчёт о переносе.txt"
)

echo.
echo  Если в отчёте всё в порядке, можно записать данные в новую программу.
echo  Перед записью автоматически делается копия новой базы.
echo.
echo    1 - записать
echo    2 - выйти, ничего не записывая
set GO=
set /p GO= Ваш выбор (1 или 2): 
if not "%GO%"=="1" goto :cancel

echo.
echo  Делаю копию новой базы перед записью...
docker compose exec -T backend python -m app.cli backup
if errorlevel 1 goto :backupfail

echo.
echo  ===== Запись =====
docker compose exec -T backend python -m app.cli import-old --db /tmp/old/old.db --apply --report /tmp/old/report.txt
if errorlevel 1 goto :applyfail
docker compose cp backend:/tmp/old/report.txt "Отчёт о переносе.txt" >nul 2>&1
docker compose exec -T backend rm -rf /tmp/old >nul 2>&1

echo.
echo  ===== Готово =====
echo  Данные перенесены. Полный отчёт: «Отчёт о переносе.txt».
echo  Пароли и фото не переносятся: пользователей создайте в разделе «Пользователи», файлы загрузите заново.
echo  Если в старой программе появились новые записи, запустите этот файл ещё раз: уже перенесённое не задвоится.
echo.
pause
exit /b 0

:cancel
docker compose exec -T backend rm -rf /tmp/old >nul 2>&1
echo.
echo  Ничего не записано. Данные в новой программе не менялись.
echo.
pause
exit /b 0

:nodocker
echo.
echo  Docker не найден. Сначала запустите новую программу файлом Запустить.bat.
pause
exit /b 1

:nodockerrun
echo.
echo  Docker не запущен. Откройте Docker Desktop, дождитесь загрузки и запустите этот файл снова.
pause
exit /b 1

:notrunning
echo.
echo  Новая программа не запущена. Запустите её файлом Запустить.bat и повторите.
pause
exit /b 1

:nofile
echo.
echo  Файл не найден: %OLD%
pause
exit /b 1

:copyfail
echo.
echo  Не удалось скопировать файл в программу. Скопируйте текст ошибки выше.
pause
exit /b 1

:previewfail
echo.
echo  Пробный запуск не удался, в новой базе ничего не изменено. Скопируйте текст ошибки выше.
pause
exit /b 1

:backupfail
echo.
echo  Не удалось сделать копию, поэтому ничего не записано.
pause
exit /b 1

:applyfail
echo.
echo  Запись не удалась. Скопируйте текст ошибки выше. Если что-то записалось частично, копия перед записью есть в разделе копий.
pause
exit /b 1
