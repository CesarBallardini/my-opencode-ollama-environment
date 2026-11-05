@echo off
rem OpenCode cloud profile, unconditionally (also inside a confidential directory).
setlocal
set "OPENCODE_CLOUD_EXE="
if exist "%USERPROFILE%\local-llm\cloud-binaries.cmd" call "%USERPROFILE%\local-llm\cloud-binaries.cmd"
if "%OPENCODE_CLOUD_EXE%"=="" (
  echo cloud profile not installed; use opencode-local 1>&2
  exit /b 1
)
endlocal & "%OPENCODE_CLOUD_EXE%" %*
