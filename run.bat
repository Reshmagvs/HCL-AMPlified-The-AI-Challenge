@echo off
REM ===========================================================================
REM  Lodestar - one-command bootstrap and run (Windows)
REM
REM    run.bat                normal start
REM    run.bat --reset        wipe venv, node_modules and the database first
REM    run.bat --backend      API only (useful before Node is installed)
REM    run.bat --no-browser   do not open a browser
REM
REM  Dependencies are reinstalled only when requirements.txt / package.json
REM  actually changed, so a second run starts in a few seconds.
REM ===========================================================================
setlocal EnableDelayedExpansion
cd /d "%~dp0"

set "DO_RESET=0"
set "BACKEND_ONLY=0"
set "OPEN_BROWSER=1"
:parse_args
if "%~1"=="" goto args_done
if /I "%~1"=="--reset"      set "DO_RESET=1"
if /I "%~1"=="--backend"    set "BACKEND_ONLY=1"
if /I "%~1"=="--no-browser" set "OPEN_BROWSER=0"
shift
goto parse_args
:args_done

echo.
echo   LODESTAR
echo   Learning is a dependency graph, not a search result.
echo   ---------------------------------------------------
echo.

REM --- 1. Locate Python -------------------------------------------------------
set "PY="
for %%C in (py python python3) do (
    if not defined PY (
        %%C -c "import sys; sys.exit(0 if sys.version_info>=(3,11) else 1)" >nul 2>&1
        if !errorlevel! equ 0 set "PY=%%C"
    )
)
if not defined PY (
    echo [X] Python 3.11+ was not found on PATH.
    echo     Install it from https://www.python.org/downloads/ with
    echo     "Add python.exe to PATH" ticked, then open a NEW terminal.
    exit /b 1
)
for /f "delims=" %%V in ('%PY% -c "import sys;print(sys.version.split()[0])"') do set "PYVER=%%V"
echo [1/7] Python %PYVER% via "%PY%"

REM --- 2. Reset if asked ------------------------------------------------------
if "%DO_RESET%"=="1" (
    echo [--] Reset requested: removing venv, node_modules and database
    if exist "backend\.venv"          rmdir /s /q "backend\.venv"
    if exist "frontend\node_modules"  rmdir /s /q "frontend\node_modules"
    if exist "backend\.install_hash"  del /q "backend\.install_hash"
    del /q "lodestar.db" "lodestar.db-wal" "lodestar.db-shm" >nul 2>&1
)

REM --- 3. Virtual environment -------------------------------------------------
set "VENV=backend\.venv"
set "VPY=%VENV%\Scripts\python.exe"
if not exist "%VPY%" (
    echo [2/7] Creating virtual environment
    %PY% -m venv "%VENV%" || (echo [X] venv creation failed & exit /b 1)
) else (
    echo [2/7] Virtual environment present
)

REM --- 4. Install backend deps only when requirements.txt changed -------------
set "REQ_HASH="
for /f "delims=" %%H in ('%PY% -c "import hashlib,pathlib;print(hashlib.sha256(pathlib.Path('backend/requirements.txt').read_bytes()).hexdigest()[:16])"') do set "REQ_HASH=%%H"
set "OLD_HASH="
if exist "backend\.install_hash" set /p OLD_HASH=<"backend\.install_hash"
if not "%REQ_HASH%"=="%OLD_HASH%" (
    echo [3/7] Installing backend dependencies ^(this happens once^)
    "%VPY%" -m pip install --quiet --upgrade pip
    "%VPY%" -m pip install --quiet -r backend\requirements.txt || (echo [X] pip install failed & exit /b 1)
    > "backend\.install_hash" echo %REQ_HASH%
) else (
    echo [3/7] Backend dependencies up to date
)

REM --- 5. Environment file ----------------------------------------------------
if not exist ".env" (
    copy /y ".env.example" ".env" >nul
    echo [4/7] Created .env from .env.example ^(mock mode, no API key needed^)
) else (
    echo [4/7] Using existing .env
)

REM --- 5b. Local language model -----------------------------------------------
REM Lodestar's language layer is local: no key, no quota, nothing leaves the
REM machine. Everything except building a *brand-new* subject works without it,
REM so a missing model is reported plainly rather than treated as fatal.
REM
REM "where ollama" is not the question -- an installed binary with no daemon
REM running looks identical to a working one from here, and the product then
REM quietly falls back to templates. So the daemon is probed, started if it is
REM installed but idle, and the model checked for by name.
echo [5a/7] Checking the local model
call :model_status
if "%MODEL_STATE%"=="1" (
    where ollama >nul 2>&1
    if errorlevel 1 (
        echo       Ollama is not installed. Lodestar runs fully without it, but cannot
        echo       build a curriculum for a subject outside the curated 152 skills.
        echo       To enable that: https://ollama.com/download
    ) else (
        echo       Ollama is installed but not running - starting it
        start "Ollama" /min cmd /c "ollama serve"
        call :wait_for_ollama
        call :model_status
    )
)

REM A server already on 8000 is not good news. The health check below only asks
REM whether *something* answers, so without this a second run would report
REM "healthy" while the browser talks to a server this script never started --
REM an older checkout, a half-dead process from a previous session. Better to
REM stop and name the problem than to test the wrong code.
"%VPY%" -c "import socket,sys; sys.exit(0 if socket.socket().connect_ex(('127.0.0.1',8000))==0 else 1)" >nul 2>&1
if not errorlevel 1 (
    echo [X] Something is already listening on 127.0.0.1:8000.
    echo     That is probably an older Lodestar API. Close its console window,
    echo     or find and stop it with:
    echo         netstat -ano ^| findstr :8000
    echo     then run this script again.
    exit /b 1
)

REM --- 6. Seed ----------------------------------------------------------------
echo [5/7] Preparing database
pushd backend
"..\%VPY%" -m scripts.seed || (echo [X] seed failed & popd & exit /b 1)
popd

REM --- 7. Start the API -------------------------------------------------------
echo [6/7] Starting API on http://127.0.0.1:8000
REM No --reload. It is a development convenience with a real cost here: the
REM reloader restarts the process on any file write, and a restart drops the
REM in-memory job table, so a subject being built in the background silently
REM stops. Editing code and re-running this script is the honest workflow.
start "Lodestar API" cmd /c "cd /d "%~dp0backend" && "..\%VPY%" -m uvicorn app.main:app --host 127.0.0.1 --port 8000"

call :wait_for_health
if errorlevel 1 (
    echo [X] The API did not become healthy. Check the "Lodestar API" window.
    exit /b 1
)

if "%BACKEND_ONLY%"=="1" (
    echo.
    echo   API ready:  http://127.0.0.1:8000/docs
    echo   Backend-only mode. Close the API window to stop.
    exit /b 0
)

REM --- 8. Frontend ------------------------------------------------------------
where node >nul 2>&1 || (
    echo [!] Node.js not found - starting backend only.
    echo     Install Node LTS from https://nodejs.org/ then re-run.
    exit /b 0
)
if not exist "frontend\node_modules" (
    echo [7/7] Installing frontend dependencies ^(this happens once^)
    pushd frontend
    call npm install --no-fund --no-audit || (echo [X] npm install failed & popd & exit /b 1)
    popd
) else (
    echo [7/7] Frontend dependencies up to date
)

echo       Starting web app on http://localhost:5173
start "Lodestar Web" cmd /c "cd /d "%~dp0frontend" && npm run dev"

if "%OPEN_BROWSER%"=="1" (
    ping -n 5 127.0.0.1 >nul
    start "" http://localhost:5173
)

echo.
echo   Web:  http://localhost:5173
echo   API:  http://127.0.0.1:8000/docs
echo   Close the two console windows to stop.
echo.
exit /b 0

REM ---------------------------------------------------------------------------
REM Prints its own verdict; MODEL_STATE is 0 ready, 1 no daemon, 2 not pulled.
:model_status
pushd backend
"..\%VPY%" -m scripts.check_model
set "MODEL_STATE=%errorlevel%"
popd
exit /b 0

REM ---------------------------------------------------------------------------
:wait_for_ollama
setlocal
for /l %%i in (1,1,20) do (
    "%~dp0%VPY%" -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:11434/api/tags',timeout=2)" >nul 2>&1
    if !errorlevel! equ 0 endlocal & exit /b 0
    ping -n 2 127.0.0.1 >nul
)
endlocal & exit /b 1

REM ---------------------------------------------------------------------------
:wait_for_health
setlocal
for /l %%i in (1,1,60) do (
    "%~dp0%VPY%" -c "import urllib.request,sys;sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=2).status==200 else 1)" >nul 2>&1
    if !errorlevel! equ 0 (
        echo       API is healthy
        endlocal & exit /b 0
    )
        REM ping is the portable one-second sleep: "timeout" reads the
        REM console and fails outright when stdin is redirected, which is
        REM exactly the situation in CI and in any scripted run.
        ping -n 2 127.0.0.1 >nul
)
endlocal & exit /b 1
