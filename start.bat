@echo off
REM ===========================================================================
REM  Lodestar - start here.
REM
REM    start.bat                normal start
REM    start.bat --reset        wipe venv, node_modules and the database first
REM    start.bat --backend      API only
REM    start.bat --no-browser   do not open a browser
REM
REM  The work is in run.bat; this exists because "start" is what people type.
REM  Both names do exactly the same thing.
REM ===========================================================================
call "%~dp0run.bat" %*
exit /b %errorlevel%
