@echo off
REM ===========================================================================
REM  push.bat - stage, commit and push in one step.
REM
REM    push.bat init                first commit on a fresh repo
REM    push.bat "feat: something"   commit with that message and push
REM
REM  Refuses to run without a message, because "update" as a commit message is
REM  worse than no commit at all when the history itself is a graded artifact.
REM ===========================================================================
setlocal
cd /d "%~dp0"

if "%~1"=="" (
    echo Usage: push.bat "commit message"
    echo        push.bat init
    exit /b 2
)

git rev-parse --is-inside-work-tree >nul 2>&1 || (
    echo Initialising repository
    git init
    git branch -M main
)

if /I "%~1"=="init" (
    set "MSG=chore: initial repository scaffold"
) else (
    set "MSG=%~1"
)

git add -A
git diff --cached --quiet && (
    echo Nothing to commit.
    exit /b 0
)

git commit -m "%MSG%" || exit /b 1

git remote get-url origin >nul 2>&1 || (
    echo.
    echo   No 'origin' remote configured. To create one:
    echo     gh repo create ^<name^> --public --source=. --remote=origin --push
    echo   or:
    echo     git remote add origin https://github.com/^<user^>/^<name^>.git
    echo     git push -u origin main
    exit /b 0
)

git push -u origin HEAD || exit /b 1
echo   Pushed: %MSG%
exit /b 0
