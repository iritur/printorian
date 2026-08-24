@echo off
REM ---------------------------------------------------------------------------
REM  Printorian - farm console
REM
REM  Brings up everything the console needs and opens it:
REM    Postgres + Redis  ->  API  ->  workers  ->  Vite
REM
REM  The console is staff-only and is served from the farm's own server on the
REM  LAN (ADR-0016). It shares this backend with the storefront, so running
REM  both scripts starts one API and two dev servers.
REM
REM  The workers are a separate process on purpose: the SLA clock recomputes
REM  lateness credits on a timer, and running it inside the API would run one
REM  copy per API worker, each recomputing the same credits.
REM
REM  ---------------------------------------------------------------------------
REM  PORTS AND DATABASE ARE DERIVED FROM THIS CHECKOUT
REM
REM  The main checkout keeps the familiar 8000 / 5174 / `printorian`. A git
REM  worktree gets its own pair of ports and its own database, named after the
REM  worktree.
REM
REM  This is not tidiness. The old script decided "is it already running?" by
REM  probing the port - which answers "is *something* on 5174", not "is *this*
REM  checkout on 5174". Start a worktree while another checkout's stack is up and
REM  it attached to the stranger, then opened the browser at it: you got the other
REM  branch's console, its API and its schema, with nothing on screen saying so.
REM  Worse, both then ran migrations against one database, so whichever branch
REM  moved first left the other unable to migrate at all.
REM
REM  Deriving the ports removes the collision instead of detecting it. Deriving
REM  the database is what makes it safe: two branches with different migrations
REM  can no longer stamp the same `alembic_version`.
REM
REM  Safe to run twice. Each step still checks whether it is already running -
REM  but now the thing it finds on the port is this checkout's, because no other
REM  checkout uses that port.
REM ---------------------------------------------------------------------------
setlocal enabledelayedexpansion
cd /d "%~dp0"

REM  One Postgres for every checkout, many databases inside it.
REM
REM  Compose names its project after the directory it is run from, so a worktree
REM  would form its own project - and then `docker compose exec postgres` cannot
REM  see the running container ("service postgres is not running"), while
REM  `docker compose up` fails outright on the fixed `container_name`. Pinning the
REM  project makes every checkout address the one server, which is what we want:
REM  the isolation that matters is the database, not the container.
set "COMPOSE_PROJECT_NAME=printorian"

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

REM --------------------------------------------------------------- identity
REM  One PowerShell call decides everything about *where* this checkout lives:
REM  its slug, its port offset and its database. Doing it in batch would mean
REM  hand-rolling a hash out of string slicing, which is exactly the kind of code
REM  nobody can read six months later.
REM
REM  Ports follow the CHECKOUT. The offset is an MD5 of the full path folded into
REM  1..40, so two worktrees land on different ports without either having to be
REM  told about the other, and a given worktree always gets the same port - which
REM  matters, because a bookmark and a running dev server have to agree from one
REM  day to the next. Switching branch must not move them.
REM
REM  The database follows the BRANCH, because the branch is what decides which
REM  migrations exist. One database shared by branches whose migration graphs have
REM  diverged is how `alembic upgrade head` ends up unable to locate the revision
REM  the database is stamped with. That is not hypothetical: it is what
REM  `0014_account` and `0015_postproduction` did to each other.
REM
REM  `main` keeps the plain `printorian` name, so the ordinary case is unchanged
REM  and the farm's own data stays where it is. Every other branch gets its own,
REM  named after it in full. The redundant `printorian` in the middle of
REM  `printorian_claude_printorian_farm_console` is ugly, and stripping it would
REM  let two differently-named branches land on one database - which is the whole
REM  failure this exists to prevent, so ugly wins.
REM
REM  A detached HEAD, or no git on PATH, falls back to naming by checkout.
REM
REM  The checkout slug drops a leading `printorian_`, because worktrees are
REM  usually named after the project and `printorian_printorian_farm_console`
REM  helps nobody.
for /f "tokens=1,2,3,4 delims=|" %%a in ('powershell -NoProfile -Command "$p = '%~dp0'.TrimEnd('\'); if ($p -match '\\\.claude\\worktrees\\([^\\]+)$') { $slug = ($Matches[1] -replace '[^A-Za-z0-9]', '_').ToLower() -replace '^printorian_', ''; $md5 = [System.Security.Cryptography.MD5]::Create(); $sum = 0; foreach ($b in $md5.ComputeHash([Text.Encoding]::UTF8.GetBytes($p))) { $sum = ($sum + $b) %% 40 }; $offset = $sum + 1 } else { $slug = ''; $offset = 0 }; $name = if ($slug) { $slug } else { 'main' }; $branch = ''; if ((Test-Path (Join-Path $p '.git')) -and (Get-Command git -ErrorAction SilentlyContinue)) { $found = @(& git -C $p rev-parse --abbrev-ref HEAD); if ($found.Count) { $branch = $found[0] } }; if (-not $branch -or $branch -eq 'HEAD') { $branch = '' }; if ($branch -and $branch -ne 'main') { $db = 'printorian_' + (($branch -replace '[^A-Za-z0-9]', '_').ToLower()) } elseif ($slug) { $db = 'printorian_' + $slug } else { $db = 'printorian' }; $shown = if ($branch) { $branch } else { 'detached' }; Write-Output ($offset.ToString() + '|' + $db + '|' + $name + '|' + $shown)"') do (
    set "OFFSET=%%a"
    set "PGDATABASE=%%b"
    set "CHECKOUT=%%c"
    set "BRANCH=%%d"
)
if not defined OFFSET (
    echo [!] Could not work out which checkout this is. Is PowerShell available?
    pause
    exit /b 1
)

set /a API_PORT=8000 + %OFFSET%
set /a WEB_PORT=5174 + %OFFSET%
set "PRINTORIAN_DATABASE_URL=postgresql+asyncpg://printorian:printorian@localhost:5433/%PGDATABASE%"
set "PRINTORIAN_API_URL=http://127.0.0.1:%API_PORT%"
set "PRINTORIAN_CONSOLE_PORT=%WEB_PORT%"

echo.
echo   Checkout :: %CHECKOUT%      Branch :: %BRANCH%
echo   API      :: %API_PORT%      Console :: %WEB_PORT%      Database :: %PGDATABASE%
echo.

echo [1/7] Database and cache...
REM  A bare `up -d` returns as soon as the containers have *started*, which is
REM  several seconds before Postgres accepts connections - and the migration on
REM  the next step then dies with "the database system is starting up".
REM
REM  Waited for explicitly rather than with `--wait`. That flag refuses to run at
REM  all on a stack containing a service with no healthcheck, and this one has
REM  `backup-init` - a one-shot that chowns the WAL archive volume and exits.
REM  Whether a given compose refuses is version-dependent, which is how a release
REM  gate passed locally and failed on the runner (f947a5e). Polling the two
REM  containers we actually depend on has no such hole, and says which one did
REM  not come up.
REM
REM  `||` rather than `if errorlevel 1` here and in every other failure check
REM  below. `if errorlevel 1` means "exit code >= 1", and a Python tool that ends
REM  in `sys.exit(-1)` - which alembic does when it cannot locate a revision -
REM  sails straight through it. That is not hypothetical: it is how a failed
REM  migration once let this script carry on to the seed and bury the real error
REM  under an unrelated traceback.
docker compose up -d || (
    echo [!] Database and cache did not come up.
    echo     If Docker Desktop is not running, start it and try again.
    echo     Otherwise check the containers:  docker compose ps
    echo                                      docker compose logs postgres
    pause
    exit /b 1
)
call :await printorian-postgres || exit /b 1
call :await printorian-redis    || exit /b 1

echo [2/7] Database "%PGDATABASE%"...
REM  A worktree's database will not exist the first time. `CREATE DATABASE` has no
REM  IF NOT EXISTS, and the second run must not fail - so the existence check is a
REM  separate query and the create only happens when it comes back empty.
for /f "tokens=*" %%d in ('docker compose exec -T postgres psql -U printorian -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname = '%PGDATABASE%'" 2^>NUL') do set "DBEXISTS=%%d"
if not defined DBEXISTS (
    docker compose exec -T postgres psql -U printorian -d postgres -c "CREATE DATABASE %PGDATABASE%" || (
        echo [!] Could not create the database. See the error above.
        pause
        exit /b 1
    )
    echo     created
) else (
    echo     already there
)

echo [3/7] Schema and first owner...
REM  Migrations before anything starts: an API against an out-of-date schema
REM  fails in ways that look like application bugs.
REM  Run from backend\: alembic.ini resolves script_location relative to the
REM  working directory, so this fails from the repo root.
pushd backend
.venv\Scripts\python.exe -m alembic upgrade head || (
    echo [!] Migrations failed. See the error above.
    echo     "Can't locate revision" means this database was last migrated by a
    echo     branch whose migrations this checkout does not have. Switch back to
    echo     that branch, or point PRINTORIAN_DATABASE_URL at a fresh database.
    popd
    pause
    exit /b 1
)
REM  A checkout with its own database starts with no accounts, and there is no way
REM  in from outside: public registration always produces a customer, and staff are
REM  created by somebody who already has an account. Without this the script
REM  finishes by opening a sign-in screen nobody can pass.
REM
REM  `provision_owner.py` reads the password from the terminal (never from an
REM  argument) and refuses on a populated database. The owner is checked for here
REM  rather than trusting that refusal, so a second run skips the prompt instead
REM  of failing on the owner it already made.
for /f "tokens=*" %%o in ('docker compose exec -T postgres psql -U printorian -d %PGDATABASE% -tAc "SELECT 1 FROM users WHERE role = 'owner' LIMIT 1" 2^>NUL') do set "OWNEREXISTS=%%o"
if defined OWNEREXISTS (
    echo     owner already there
) else (
    .venv\Scripts\python.exe tools\provision_owner.py --email boss@printorian.example || (
        echo [!] Could not create the first owner. See the error above.
        popd
        pause
        exit /b 1
    )
)
REM  The packing bench cannot open without its instruction and its tara: a parcel
REM  raised against no published instruction has no steps and no norm, and an
REM  empty shelf has no box to recommend. Idempotent - tara is keyed by code and
REM  an already-published version is left alone - so running it every start costs
REM  nothing and a fresh checkout gets a working post.
.venv\Scripts\python.exe scripts\seed_packaging.py || (
    echo [!] Could not seed the packing bench. See the error above.
    popd
    pause
    exit /b 1
)
popd

echo [4/7] API on port %API_PORT%...
curl -s -m 2 -o NUL http://127.0.0.1:%API_PORT%/health
if errorlevel 1 (
    REM No CORS. Both apps reach the API through their own dev proxy on the same
    REM origin, exactly as they do in production - the storefront behind the
    REM tunnel and the console on the farm LAN (ADR-0016). The desktop shell that
    REM needed cross-origin access is gone.
    start "Printorian API (%CHECKOUT%)" /d "%~dp0backend" cmd /k .venv\Scripts\python.exe -m uvicorn printorian.api.app:create_app --factory --reload --port %API_PORT%
    echo     started in a new window
) else (
    echo     already running - reusing it
)

echo [5/7] Waiting for the API...
set /a tries=0
:wait_api
curl -s -m 2 -o NUL http://127.0.0.1:%API_PORT%/health && goto api_ready
set /a tries+=1
if !tries! GEQ 30 (
    echo [!] The API did not answer in 30 tries. Check its window for the error.
    pause
    exit /b 1
)
ping -n 2 127.0.0.1 >NUL
goto wait_api
:api_ready
echo     ready

echo [6/7] Background workers...
REM No port to probe, so the check looks for the process itself - and for *this*
REM checkout's, which is what the database name in the match is doing. Without it
REM a worktree would see the main checkout's workers and start none of its own,
REM leaving its own SLA clock and post-production sweep stopped.
REM
REM It matches the `cmd` wrapper rather than the python process, because the
REM database name is on the wrapper's command line and not on python's. That
REM means the old `Name -like 'python*'` filter cannot be used, and it was not
REM cosmetic: without it the query matches *itself*, since a PowerShell process
REM searching for a string has that string on its own command line. Excluding
REM the shells by name does the same job.
powershell -NoProfile -Command "if (Get-CimInstance Win32_Process | Where-Object { $_.Name -notlike 'powershell*' -and $_.Name -notlike 'pwsh*' -and $_.CommandLine -like '*printorian.workers*' -and $_.CommandLine -like '*%PGDATABASE%*' }) { exit 0 }; exit 1"
if errorlevel 1 (
    REM The database goes on the command line rather than only in the environment
    REM so the check above can see it. It is a local dev credential in a local dev
    REM script; nothing here is a secret.
    start "Printorian Workers (%CHECKOUT%)" /d "%~dp0backend" cmd /k "set PRINTORIAN_DATABASE_URL=%PRINTORIAN_DATABASE_URL%&& .venv\Scripts\python.exe -m printorian.workers"
    echo     started in a new window
) else (
    echo     already running - reusing it
)

echo [7/7] Console on port %WEB_PORT%...
curl -s -m 2 -o NUL http://127.0.0.1:%WEB_PORT%/
if errorlevel 1 (
    start "Printorian Console (%CHECKOUT%)" /d "%~dp0frontend" cmd /k npm run dev --workspace @printorian/console
) else (
    echo     already running - reusing it
)

echo.
echo   Console:     http://127.0.0.1:%WEB_PORT%
echo   API docs:    http://127.0.0.1:%API_PORT%/docs
echo   Database:    %PGDATABASE%
echo.
echo   Tip: if a window stops responding, click in it and press Esc. Windows
echo        console QuickEdit freezes a process when text is selected.
echo.
ping -n 4 127.0.0.1 >NUL
start http://127.0.0.1:%WEB_PORT%
endlocal
exit /b 0

REM ---------------------------------------------------------------------------
REM  Wait for one container to report healthy, or say which one did not.
REM  Three minutes at three seconds a poll: a cold volume runs crash recovery
REM  before Postgres answers, and failing early there is the same race in the
REM  other direction.
:await
setlocal
set "name=%~1"
for /l %%i in (1,1,60) do (
    for /f "delims=" %%s in ('docker inspect -f "{{.State.Health.Status}}" %name% 2^>NUL') do (
        if "%%s"=="healthy" endlocal & exit /b 0
    )
    ping -n 3 127.0.0.1 >NUL
)
echo [!] %name% did not become healthy.
docker logs --tail 40 %name%
pause
endlocal & exit /b 1
