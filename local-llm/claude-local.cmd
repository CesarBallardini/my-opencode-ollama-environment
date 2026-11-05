@echo off
rem Claude Code against the local Ollama only (Anthropic-format /v1/messages on 127.0.0.1).
rem Own CLAUDE_CONFIG_DIR, so the cloud login and history are never read; every model slot
rem points at the local model (LOCAL_LLM_MODEL, default qwen3-coder-32k); non-essential traffic off; web access only through the egress
rem proxy, which refuses Anthropic, Qwen/Alibaba and opencode domains. The binary is a private
rem copy that the "local-llm: claude direct outbound" firewall rule blocks.
setlocal
call "%USERPROFILE%\local-llm\ensure-proxy.cmd" || exit /b 1
set "ROOT=%USERPROFILE%\local-llm\claude"
set "CLAUDE_CONFIG_DIR=%ROOT%\config"
set ANTHROPIC_BASE_URL=http://127.0.0.1:11434
set ANTHROPIC_AUTH_TOKEN=ollama
set ANTHROPIC_API_KEY=
if not defined LOCAL_LLM_MODEL set LOCAL_LLM_MODEL=qwen3-coder-32k
set ANTHROPIC_MODEL=%LOCAL_LLM_MODEL%
set ANTHROPIC_DEFAULT_OPUS_MODEL=%LOCAL_LLM_MODEL%
set ANTHROPIC_DEFAULT_SONNET_MODEL=%LOCAL_LLM_MODEL%
set ANTHROPIC_DEFAULT_HAIKU_MODEL=%LOCAL_LLM_MODEL%
set ANTHROPIC_SMALL_FAST_MODEL=%LOCAL_LLM_MODEL%
set CLAUDE_CODE_SUBAGENT_MODEL=%LOCAL_LLM_MODEL%
set CLAUDE_CODE_MAX_OUTPUT_TOKENS=8192
rem Context window of each local model, as set by num_ctx in its Modelfile.
set LOCAL_LLM_CONTEXT=32768
if /i "%LOCAL_LLM_MODEL%"=="gpt-oss-64k" set LOCAL_LLM_CONTEXT=65536
set CLAUDE_CODE_AUTO_COMPACT_WINDOW=%LOCAL_LLM_CONTEXT%
set CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1
set DISABLE_TELEMETRY=1
set DISABLE_ERROR_REPORTING=1
set DISABLE_AUTOUPDATER=1
set DISABLE_BUG_COMMAND=1
set DISABLE_PROMPT_CACHING=1
set CLAUDE_CODE_DISABLE_FEEDBACK_SURVEY=1
set CLAUDE_CODE_DISABLE_OFFICIAL_MARKETPLACE_AUTOINSTALL=1
"%ROOT%\bin\claude.exe" %*
