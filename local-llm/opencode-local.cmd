@echo off
rem opencode against the local Ollama only. Own config/data/cache/state, so the cloud
rem profile's auth.json is never read; vendor-facing features disabled; web access only through
rem the egress proxy, which refuses Anthropic, Qwen/Alibaba and opencode domains. The binary is a
rem private copy that the "local-llm: opencode direct outbound" firewall rule blocks.
setlocal
call "%USERPROFILE%\local-llm\ensure-proxy.cmd" || exit /b 1
set "ROOT=%USERPROFILE%\local-llm\opencode"
set "XDG_CONFIG_HOME=%ROOT%\config"
set "XDG_DATA_HOME=%ROOT%\data"
set "XDG_CACHE_HOME=%ROOT%\cache"
set "XDG_STATE_HOME=%ROOT%\state"
set "OPENCODE_CONFIG_DIR=%ROOT%\config\opencode"
set OPENCODE_DISABLE_MODELS_FETCH=true
set OPENCODE_DISABLE_AUTOUPDATE=true
set OPENCODE_DISABLE_SHARE=true
set OPENCODE_DISABLE_DEFAULT_PLUGINS=true
set OPENCODE_DISABLE_LSP_DOWNLOAD=true
set OPENCODE_DISABLE_EXTERNAL_SKILLS=true
set OPENCODE_DISABLE_CLAUDE_CODE=true
set OPENCODE_PURE=true
set ANTHROPIC_API_KEY=
set OPENAI_API_KEY=
set OPENCODE_API_KEY=
"%ROOT%\bin\opencode.exe" %*
