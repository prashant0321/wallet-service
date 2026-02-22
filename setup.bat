@echo off
REM =============================================================================
REM setup.bat — Windows bootstrap script for the Wallet Service
REM =============================================================================
REM Usage: Double-click or run from PowerShell:  .\setup.bat
REM =============================================================================

echo.
echo [1/4] Copying .env.example to .env (if not already present)...
IF NOT EXIST .env (
    copy .env.example .env
    echo Created .env
) ELSE (
    echo .env already exists, skipping.
)

echo.
echo [2/4] Starting PostgreSQL container...
docker compose up -d db

echo.
echo [3/4] Waiting for PostgreSQL to be ready (up to 30s)...
SET /A counter=0
:WAIT_LOOP
docker exec wallet_db pg_isready -U wallet_user -d wallet_db >nul 2>&1
IF %ERRORLEVEL% == 0 GOTO READY
IF %counter% GEQ 30 (
    echo ERROR: PostgreSQL did not become ready in time.
    exit /b 1
)
SET /A counter=%counter%+1
timeout /t 1 /nobreak >nul
GOTO WAIT_LOOP

:READY
echo PostgreSQL is ready.

echo.
echo [4/4] Starting the Wallet API...
docker compose up -d api

echo.
echo ================================================
echo  Wallet Service is running!
echo.
echo  API:   http://localhost:8000
echo  Docs:  http://localhost:8000/docs
echo  ReDoc: http://localhost:8000/redoc
echo ================================================
