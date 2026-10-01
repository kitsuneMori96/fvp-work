@echo off
rem savefix 构建脚本 (需 MSVC x86; 已验证 BuildTools 14.51)
setlocal
set VCVARS=D:\soft\BuildTools\VC\Auxiliary\Build\vcvars32.bat
if not exist "%VCVARS%" (
  echo 找不到 %VCVARS%
  echo 请把第一行的 D:\soft\BuildTools 改成你机器上 VS 生成工具的实际路径
  pause
  exit /b 1
)
call "%VCVARS%" >nul
cd /d %~dp0src
cl /nologo /O2 /MT /W3 /D_WIN32_WINNT=0x0601 /LD savefix.c /link /MACHINE:X86 /OUT:..\savefix.dll
if errorlevel 1 ( echo savefix.dll 编译失败 & pause & exit /b 1 )
cl /nologo /O2 /MT /W3 /D_WIN32_WINNT=0x0601 SaveFixLauncher.c /link /MACHINE:X86 /SUBSYSTEM:WINDOWS /OUT:..\SaveFixLauncher.exe
if errorlevel 1 ( echo SaveFixLauncher.exe 编译失败 & pause & exit /b 1 )
echo.
echo OK:
dir ..\savefix.dll ..\SaveFixLauncher.exe
echo.
echo 下一步: 把这两个文件拷到游戏目录 (Sakura.exe 旁边), 双击 SaveFixLauncher.exe
pause
