@echo off
REM ---------------------------------------------------------------------------
REM  Printorian - design kit
REM
REM  Serves design\ as static files on :4180 and opens the component reference.
REM
REM  Nothing else is needed: the kit is HTML and CSS with no backend, which is
REM  the whole point of it - the look is agreed before anything is wired up.
REM  No database, no API, no virtualenv.
REM
REM  This exists because the path is easy to get wrong. `http-server design`
REM  serves the *design* folder relative to the current directory, so running it
REM  from inside design\ looks for design\design and answers 404 to everything.
REM  `cd /d "%~dp0"` below pins the working directory to the repo root, so the
REM  script behaves the same wherever it is launched from.
REM
REM  Safe to run twice - it reuses a server already listening on the port.
REM ---------------------------------------------------------------------------
setlocal
cd /d "%~dp0"

if not exist "design\index.html" (
    echo [!] design\index.html not found.
    echo     This script must sit in the repository root, next to the design folder.
    pause
    exit /b 1
)

where npx >NUL 2>&1
if errorlevel 1 (
    echo [!] npx was not found on PATH. Install Node.js 24+ and try again.
    pause
    exit /b 1
)

echo [1/2] Design kit on port 4180...
curl -s -m 2 -o NUL http://127.0.0.1:4180/
if errorlevel 1 (
    REM -c-1 disables caching: the kit is edited and reloaded constantly, and a
    REM cached stylesheet is a change that appears not to have happened.
    REM
    REM The first run downloads http-server, which needs the internet. The farm
    REM itself does not (ADR-0003) - this is a developer machine tool, and the
    REM kit is static files that any web server can host.
    start "Printorian Design Kit" /d "%~dp0" cmd /k npx --yes http-server design -p 4180 -c-1
    echo     started in a new window
) else (
    echo     already running - reusing it
)

echo [2/2] Waiting for it to answer...
set /a tries=0
:wait_kit
curl -s -m 2 -o NUL http://127.0.0.1:4180/ && goto kit_ready
set /a tries+=1
if %tries% GEQ 30 (
    echo [!] Port 4180 did not answer in 30 tries. Check its window for the error.
    pause
    exit /b 1
)
ping -n 2 127.0.0.1 >NUL
goto wait_kit
:kit_ready
echo     ready

echo.
echo   Design kit:  http://127.0.0.1:4180
echo.
echo   index.html is the component reference and links to all 21 screens.
echo   Nothing here is wired to the backend - it is the visual language only.
echo.
echo   Tip: if a window stops responding, click in it and press Esc. Windows
echo        console QuickEdit freezes a process when text is selected.
echo.
ping -n 2 127.0.0.1 >NUL
start http://127.0.0.1:4180
endlocal
