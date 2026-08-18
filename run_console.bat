@echo off
REM ---------------------------------------------------------------------------
REM  Printorian - farm console
REM
REM  Brings up everything the console needs and opens it:
REM    Postgres + Redis  ->  API on :8000  ->  workers  ->  Vite on :5174
REM
REM  The console is staff-only and is served from the farm's own server on the
REM  LAN (ADR-0016). It shares this backend with the storefront, so running
REM  both scripts starts one API and two dev servers.
REM
REM  The workers are a separate process on purpose: the SLA clock recomputes
REM  lateness credits on a timer, and running it inside the API would run one
REM  copy per API worker, each recomputing the same credits.
REM
REM  Safe to run twice. Each step checks whether it is already running, so this
REM  will not start a second API and collide on the port - which is also what
REM  lets run_web.bat and run_app.bat share one backend.
REM ---------------------------------------------------------------------------
setlocal
cd /d "%~dp0"

if not exist "backend\.venv\Scripts\python.exe" (
    echo [!] No Python virtualenv found at backend\.venv
    echo     Create it first:
    echo       cd backend ^&^& python -m venv .venv ^&^& .venv\Scripts\python -m pip install -e ".[dev]"
    pause
    exit /b 1
)
if not exist "frontend\node_modules" (
    echo [!] Frontend dependencies are not installed.
    echo     Run: cd frontend ^&^& npm install
    pause
    exit /b 1
)

echo [1/5] Database and cache...
REM  --wait, not a bare `up -d`. Without it compose returns as soon as the
REM  containers have *started*, which is several seconds before Postgres accepts
REM  connections - and the migration on the next step then dies with
REM  "the database system is starting up". Both services already declare a
REM  healthcheck; this is what makes compose honour them.
REM
REM  The timeout is generous because a cold volume runs crash recovery first, and
REM  failing early there would be the same race in the other direction.
docker compose up -d --wait --wait-timeout 120
if errorlevel 1 (
    echo [!] Database and cache did not come up.
    echo     If Docker Desktop is not running, start it and try again.
    echo     Otherwise check the containers:  docker compose ps
    echo                                      docker compose logs postgres
    pause
    exit /b 1
)

echo [2/5] API on port 8000...
curl -s -m 2 -o NUL http://127.0.0.1:8000/health
if errorlevel 1 (
    REM Migrations first: starting against an out-of-date schema fails in ways
    REM that look like application bugs.
    REM Run from backend\: alembic.ini resolves script_location relative to the
    REM working directory, so this fails from the repo root.
    pushd backend
    .venv\Scripts\python.exe -m alembic upgrade head
    if errorlevel 1 (
        echo [!] Migrations failed. See the error above.
        popd
        pause
        exit /b 1
    )
    popd
    REM No CORS. Both apps reach the API through their own dev proxy on the same
    REM origin, exactly as they do in production - the storefront behind the
    REM tunnel and the console on the farm LAN (ADR-0016). The desktop shell that
    REM needed cross-origin access is gone.
    start "Printorian API" /d "%~dp0backend" cmd /k .venv\Scripts\python.exe -m uvicorn printorian.api.app:create_app --factory --reload
    echo     started in a new window
) else (
    echo     already running - reusing it
)

echo [3/5] Waiting for the API...
set /a tries=0
:wait_api
curl -s -m 2 -o NUL http://127.0.0.1:8000/health && goto api_ready
set /a tries+=1
if %tries% GEQ 30 (
    echo [!] The API did not answer in 30 tries. Check its window for the error.
    pause
    exit /b 1
)
ping -n 2 127.0.0.1 >NUL
goto wait_api
:api_ready
echo     ready

echo [4/5] Background workers...
REM No port to probe, so the check looks for the process itself. Enumerating
REM every process is slower than a filtered query but needs no nested quoting,
REM which a .bat mangles.
REM
REM The name filter is not cosmetic: without it this command matches *itself*,
REM because its own command line contains the string it searches for - so the
REM check would report "already running" every time and never start anything.
powershell -NoProfile -Command "if (Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'python*' -and $_.CommandLine -like '*printorian.workers*' }) { exit 0 }; exit 1"
if errorlevel 1 (
    start "Printorian Workers" /d "%~dp0backend" cmd /k .venv\Scripts\python.exe -m printorian.workers
    echo     started in a new window
) else (
    echo     already running - reusing it
)

echo [5/5] Console on port 5174...
curl -s -m 2 -o NUL http://127.0.0.1:5174/
if errorlevel 1 (
    start "Printorian Console" /d "%~dp0frontend" cmd /k npm run dev --workspace @printorian/console
) else (
    echo     already running - reusing it
)

echo.
echo   Console:     http://127.0.0.1:5174
echo   API docs:    http://127.0.0.1:8000/docs
echo.
echo   Tip: if a window stops responding, click in it and press Esc. Windows
echo        console QuickEdit freezes a process when text is selected.
echo.
ping -n 4 127.0.0.1 >NUL
start http://127.0.0.1:5174
endlocal
