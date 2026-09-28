@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

set "PY="
where python >nul 2>nul && set "PY=python"
if not defined PY (
  echo [ОШИБКА] Python не найден в PATH. Установите Python 3.11+ и повторите.
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo [1/3] Создаю виртуальное окружение...
  %PY% -m venv .venv || goto :fail
)

set "VENV_PY=.venv\Scripts\python.exe"

"%VENV_PY%" -c "import fastapi, uvicorn, multipart" >nul 2>nul
if errorlevel 1 (
  echo [2/3] Ставлю зависимости...
  "%VENV_PY%" -m pip install --quiet --upgrade pip
  "%VENV_PY%" -m pip install --quiet -r requirements.txt || goto :fail
) else (
  echo [2/3] Зависимости уже установлены.
)

echo [3/3] Запускаю сервер на http://127.0.0.1:8756
start "" http://127.0.0.1:8756
"%VENV_PY%" -m uvicorn server.main:app --host 127.0.0.1 --port 8756 --app-dir . --log-level warning
goto :eof

:fail
echo.
echo [ОШИБКА] Не удалось подготовить окружение. Проверьте вывод выше.
pause
exit /b 1
