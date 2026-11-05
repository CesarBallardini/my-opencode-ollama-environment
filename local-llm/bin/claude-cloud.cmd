@echo off
rem Claude Code cloud profile, unconditionally (also inside a confidential directory).
setlocal
set "CLAUDE_CLOUD_EXE="
if exist "%USERPROFILE%\local-llm\cloud-binaries.cmd" call "%USERPROFILE%\local-llm\cloud-binaries.cmd"
if "%CLAUDE_CLOUD_EXE%"=="" (
  echo cloud profile not installed; use claude-local 1>&2
  exit /b 1
)
endlocal & "%CLAUDE_CLOUD_EXE%" %*
