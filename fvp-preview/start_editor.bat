@echo off
REM fvp-preview Windows 一键启动（原生运行，不依赖 WSL）。
REM 用法：start_editor.bat [SAMPLE_DIR] [PORT]
REM 环境变量：FVP_BASE_PATH（必填，游戏目录，如 D:\soft\Sakura moyu）
REM   SIMPLE_DIR / SCRIPT（剧本编辑链）、FVP_HCB / FVP_ADDRMAP / FVP_OUT（写回）
chcp 65001 >nul
set "SAMPLE_DIR=%~1"
if "%SAMPLE_DIR%"=="" set "SAMPLE_DIR=%TEMP%\fvp-sample"
set "PORT=%~2"
if "%PORT%"=="" set "PORT=8000"
set "REPO=%~dp0"
if not exist "%SAMPLE_DIR%\scene.json" (
  echo 示例工程不存在，先物化：
  echo   set FVP_BASE_PATH=D:\soft\Sakura moyu
  echo   bash %REPO%sample_tachie.sh %SAMPLE_DIR%  ^(需 WSL 或手动跑管线^)
  exit /b 1
)
if "%FVP_VM_BIN%"=="" (
  if exist "%REPO%bin\fvp-preview-vm.exe" set "FVP_VM_BIN=%REPO%bin\fvp-preview-vm.exe"
)
if "%FVP_BASE_PATH%"=="" (
  echo 必须先设置游戏目录：set FVP_BASE_PATH=D:\soft\Sakura moyu
  exit /b 1
)
if "%FVP_VFS_SKIP%"=="" set "FVP_VFS_SKIP=voice,bgm,se,se_sys,se_env"
set ARGS=--sample-dir "%SAMPLE_DIR%" --port %PORT%
if not "%FVP_HCB%"=="" set ARGS=%ARGS% --hcb "%FVP_HCB%" --addrmap "%FVP_ADDRMAP%" --out-hcb "%FVP_OUT%"
if not "%SIMPLE_DIR%"=="" set ARGS=%ARGS% --simple-dir "%SIMPLE_DIR%"
if not "%SCRIPT%"=="" set ARGS=%ARGS% --script "%SCRIPT%"
echo === fvp-preview web ===
echo 浏览器开：http://localhost:%PORT%/
start "" "http://localhost:%PORT%/"
where py >nul 2>nul
if %ERRORLEVEL%==0 (
  py -3 "%REPO%serve_editor.py" %ARGS%
) else (
  python "%REPO%serve_editor.py" %ARGS%
)
