@echo off
setlocal EnableExtensions EnableDelayedExpansion
title ContractLens AI - local launcher
rem ---------------------------------------------------------------------------
rem  ContractLens AI - Windows launcher
rem  Safe to run repeatedly. Never deletes files or resets the database.
rem ---------------------------------------------------------------------------
cd /d "%~dp0"
set "ROOT=%CD%"
set "BACKEND=%ROOT%\backend"
set "FRONTEND=%ROOT%\frontend"
set "VENV_PY=%BACKEND%\.venv\Scripts\python.exe"

echo.
echo  ==============================================
echo    ContractLens AI - starting local environment
echo  ==============================================
echo.

rem --- 1. Required software ----------------------------------------------------
set "PY_CMD="
where py >nul 2>&1 && (py -3.12 -c "import sys" >nul 2>&1 && set "PY_CMD=py -3.12")
if not defined PY_CMD (
  where python >nul 2>&1 && (python -c "import sys; sys.exit(0 if sys.version_info >= (3,12) else 1)" >nul 2>&1 && set "PY_CMD=python")
)
if not defined PY_CMD (
  echo [ERROR] Python 3.12 or newer is required. Install it from https://www.python.org/downloads/
  goto :fail
)
echo [OK] Python found: %PY_CMD%

where node >nul 2>&1 || (echo [ERROR] Node.js 20 or newer is required. Install it from https://nodejs.org/ & goto :fail)
where npm >nul 2>&1 || (echo [ERROR] npm was not found. Reinstall Node.js from https://nodejs.org/ & goto :fail)
for /f "delims=" %%v in ('node --version') do echo [OK] Node.js %%v

set "HAVE_DOCKER=0"
where docker >nul 2>&1 && (docker info >nul 2>&1 && set "HAVE_DOCKER=1")
if "%HAVE_DOCKER%"=="1" (echo [OK] Docker is running) else (echo [WARN] Docker is not running - start Docker Desktop to use the bundled PostgreSQL database.)

rem --- 2. Backend dependencies ------------------------------------------------
if not exist "%VENV_PY%" (
  echo [..] Creating Python virtual environment in backend\.venv
  %PY_CMD% -m venv "%BACKEND%\.venv" || (echo [ERROR] Could not create the virtual environment. & goto :fail)
)
echo [..] Installing / updating backend dependencies
"%VENV_PY%" -m pip install --disable-pip-version-check -q -r "%BACKEND%\requirements.txt" || (echo [ERROR] Backend dependency installation failed. Check your internet connection. & goto :fail)
echo [OK] Backend dependencies installed

rem --- 3. Frontend dependencies -----------------------------------------------
if not exist "%FRONTEND%\node_modules" (
  echo [..] Installing frontend dependencies - the first run can take a few minutes
  pushd "%FRONTEND%"
  call npm ci --no-audit --no-fund || (popd & echo [ERROR] Frontend dependency installation failed. & goto :fail)
  popd
)
echo [OK] Frontend dependencies installed

rem --- 4. Configuration (created once, never overwritten) -----------------------
if not exist "%BACKEND%\.env" (
  echo [..] Creating backend\.env from .env.example with freshly generated secrets
  "%VENV_PY%" -c "import secrets,re,pathlib;from cryptography.fernet import Fernet;src=pathlib.Path(r'%ROOT%\.env.example').read_text(encoding='utf-8');src=re.sub(r'(?m)^JWT_SECRET_KEY=.*$','JWT_SECRET_KEY='+secrets.token_urlsafe(48),src);src=re.sub(r'(?m)^ENCRYPTION_KEY=.*$','ENCRYPTION_KEY='+Fernet.generate_key().decode(),src);pathlib.Path(r'%BACKEND%\.env').write_text(src,encoding='utf-8')" || (echo [ERROR] Could not create backend\.env & goto :fail)
  echo [OK] backend\.env created - AI is off until you add a provider key there
) else (
  echo [OK] Using existing backend\.env
)

rem --- 5. Database ---------------------------------------------------------------
if "%HAVE_DOCKER%"=="1" (
  call :startdb || goto :fail
) else (
  echo [WARN] Continuing without Docker: DATABASE_URL in backend\.env must point to a running PostgreSQL server.
)

echo [..] Applying database migrations
pushd "%BACKEND%"
"%VENV_PY%" -m alembic upgrade head || (popd & echo [ERROR] Database migrations failed - check DATABASE_URL in backend\.env and that PostgreSQL is running. & goto :fail)
popd
echo [OK] Database is up to date

rem --- 6. Start servers ---------------------------------------------------------
netstat -ano | findstr /R /C:":8000 .*LISTENING" >nul && (echo [WARN] Port 8000 is already in use - an API server may already be running.)
netstat -ano | findstr /R /C:":5173 .*LISTENING" >nul && (echo [WARN] Port 5173 is already in use - the web app may already be running.)
echo [..] Starting API on http://127.0.0.1:8000 and web app on http://localhost:5173
start "ContractLens API" cmd /k "cd /d "%BACKEND%" && "%VENV_PY%" -m uvicorn app.main:app --host 127.0.0.1 --port 8000"
start "ContractLens Web" cmd /k "cd /d "%FRONTEND%" && npm run dev"
timeout /t 6 /nobreak >nul
start "" http://localhost:5173
echo.
echo  ContractLens AI is starting in two new windows. Close them to stop the app.
echo  Web app: http://localhost:5173      API docs: http://127.0.0.1:8000/api/docs
echo.
pause
exit /b 0

:startdb
echo [..] Starting PostgreSQL container
docker compose up -d db || (echo [ERROR] Could not start the database container. & exit /b 1)
set /a TRIES=0
:waitdb
docker compose exec -T db pg_isready -U contractlens -d contractlens >nul 2>&1 && (echo [OK] PostgreSQL is ready on 127.0.0.1:5437 & exit /b 0)
set /a TRIES+=1
if %TRIES% GEQ 40 (echo [ERROR] The database did not become ready in time. & exit /b 1)
timeout /t 1 /nobreak >nul
goto :waitdb

:fail
echo.
echo  Startup stopped. Fix the problem above and run "Start App.bat" again.
pause
exit /b 1
