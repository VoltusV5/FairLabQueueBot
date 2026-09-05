@echo off
if "%~1"=="" (
    echo Usage: check.bat ^<path_to_file^>
    exit /b 1
)

set FILE=%~1

echo Running checks for %FILE%...

ruff check %FILE%
if %ERRORLEVEL% neq 0 (
    setlocal enabledelayedexpansion
    set /p CHOICE="Auto-fix ruff errors? (y/n): "
    if /i "!CHOICE!"=="y" (
        endlocal
        ruff check --fix %FILE%
        ruff format %FILE%
        if %ERRORLEVEL% neq 0 exit /b %ERRORLEVEL%
    ) else (
        endlocal
        exit /b %ERRORLEVEL%
    )
)

mypy --strict %FILE%
if %ERRORLEVEL% neq 0 exit /b %ERRORLEVEL%

bandit -c .bandit %FILE%
if %ERRORLEVEL% neq 0 exit /b %ERRORLEVEL%

echo.
echo All checks passed successfully!
