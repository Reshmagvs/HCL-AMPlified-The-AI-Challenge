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

REM --- 6. Seed ----------------------------------------------------------------
echo [5/7] Preparing database
pushd backend
"..\%VPY%" -m scripts.seed || (echo [X] seed failed & popd & exit /b 1)
popd

REM --- 7. Start the API -------------------------------------------------------
echo [6/7] Starting API on http://127.0.0.1:8000
start "Lodestar API" cmd /c "cd /d "%~dp0backend" && "..\%VPY%" -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload"

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
