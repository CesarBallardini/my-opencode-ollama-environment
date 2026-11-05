@echo off
rem Start the egress proxy on 127.0.0.1:8899 unless it is already listening, then point this
rem shell's HTTP(S) traffic at it. Loopback (Ollama) bypasses it.
netstat -ano | findstr /R /C:"127\.0\.0\.1:8899 .*LISTENING" >nul
if errorlevel 1 (
  start "local-llm egress proxy" /min uv run --no-project --python 3.14 python -I "%USERPROFILE%\local-llm\egress_proxy.py"
  for /L %%i in (1,1,20) do (
    netstat -ano | findstr /R /C:"127\.0\.0\.1:8899 .*LISTENING" >nul && goto :ready
    "%SystemRoot%\System32\PING.EXE" -n 2 127.0.0.1 >nul
  )
  echo egress proxy did not start on 127.0.0.1:8899; refusing to launch. 1>&2
  exit /b 1
)
:ready
set "HTTPS_PROXY=http://127.0.0.1:8899"
set "HTTP_PROXY=http://127.0.0.1:8899"
set "https_proxy=http://127.0.0.1:8899"
set "http_proxy=http://127.0.0.1:8899"
set "NO_PROXY=127.0.0.1,localhost,::1"
set "no_proxy=127.0.0.1,localhost,::1"
exit /b 0
