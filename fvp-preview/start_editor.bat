@echo off
REM fvp-preview launcher (Windows native, no WSL required).
REM Usage: start_editor.bat [SAMPLE_DIR] [PORT]
REM Env: FVP_BASE_PATH (required, game dir, e.g. D:\soft\Sakura moyu)
REM   SIMPLE_DIR / SCRIPT (script chain), FVP_HCB / FVP_ADDRMAP / FVP_OUT (writeback)
setlocal EnableExtensions EnableDelayedExpansion
set "SAMPLE_DIR=%~1"
if "%SAMPLE_DIR%"=="" set "SAMPLE_DIR=%TEMP%\fvp-sample"
set "PORT=%~2"
if "%PORT%"=="" set "PORT=8003"
set "REPO=%~dp0"
if not exist "%SAMPLE_DIR%\scene.json" (
  echo Sample project not found: %SAMPLE_DIR%
  echo Put scene.json/replay.json/addrmap.json there first, then retry.
  echo (Or pass the dir as arg: start_editor.bat "D:\path\to\sample" [PORT])
  goto :fail
)
if "%FVP_VM_BIN%"=="" (
  if exist "%REPO%bin\fvp-preview-vm.exe" set "FVP_VM_BIN=%REPO%bin\fvp-preview-vm.exe"
)
if "%FVP_BASE_PATH%"=="" (
  echo FVP_BASE_PATH is not set. Example:
  echo   set FVP_BASE_PATH=D:\soft\Sakura moyu
  goto :fail
)
if "%FVP_VFS_SKIP%"=="" set "FVP_VFS_SKIP=voice,bgm,se,se_sys,se_env"
where py >nul 2>nul
if %ERRORLEVEL%==0 ( set "PY=py -3" ) else ( set "PY=python" )
%PY% --version >nul 2>nul
if %ERRORLEVEL% NEQ 0 (
  echo No Python found. Install Python 3 and retry.
  goto :fail
)
REM Refuse to start when the port is already taken (e.g. another local server).
REM NOTE: curl.exe on some machines hangs on localhost, so use PowerShell TCP probe.
REM (no double quotes inside: they would break the set quoting.)
set PROBE=powershell -NoProfile -Command $c=New-Object Net.Sockets.TcpClient; try { $c.Connect('127.0.0.1',%PORT%); $c.Close(); exit 0 } catch { exit 7 }
%PROBE% >nul 2>nul
if !ERRORLEVEL!==0 (
  echo Port %PORT% is already in use. Stop the program occupying it,
  echo or run: start_editor.bat "%SAMPLE_DIR%" ^<another-port^>
  goto :fail
)
set ARGS=--sample-dir "%SAMPLE_DIR%" --port %PORT%
if not "%FVP_HCB%"=="" set ARGS=%ARGS% --hcb "%FVP_HCB%" --addrmap "%FVP_ADDRMAP%" --out-hcb "%FVP_OUT%"
if not "%SIMPLE_DIR%"=="" set ARGS=%ARGS% --simple-dir "%SIMPLE_DIR%"
if not "%SCRIPT%"=="" set ARGS=%ARGS% --script "%SCRIPT%"
echo === fvp-preview web ===
start "fvp-preview" cmd /k "chcp 65001>nul & %PY% "%REPO%serve_editor.py" %ARGS%"
echo Waiting for the server, then opening the browser...
for /L %%i in (1,1,20) do (
  %PROBE% >nul 2>nul
  if !ERRORLEVEL!==0 goto :ready
  timeout /t 1 /nobreak >nul
)
echo Server did not respond. Check the fvp-preview window for errors.
goto :fail
:ready
start "" "http://localhost:%PORT%/"
echo Browser opened: http://localhost:%PORT%/
echo Close the fvp-preview window to stop the server.
exit /b 0
:fail
echo.
echo (Startup failed; window kept open so you can read the message.)
pause
exit /b 1
