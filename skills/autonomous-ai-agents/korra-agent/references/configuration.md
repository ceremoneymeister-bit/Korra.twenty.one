# Configuration, Toolsets & Voice

Edit with `korra config edit` or `korra config set section.key value`.
Source of truth: `korra config --help` and the configuration code in the
installed build.

### Config Sections (most-used keys)

| Section | Key options |
|---------|-------------|
| `model` | `default`, `provider`, `base_url`, `api_key`, `context_length`, `aliases` |
| `agent` | `max_turns` (90), `tool_use_enforcement`, `service_tier`, `verify_on_stop` |
| `terminal` | `backend` (local/docker/ssh/modal/daytona/singularity), `cwd`, `timeout` (180) |
| `compression` | `enabled`, `threshold` (0.50), `target_ratio` (0.20) |
| `display` | `interface` (cli/tui), `language`, `show_reasoning`, `show_cost`, `memory_notifications` |
| `approvals` | `mode` (smart/manual/off), `timeout`, `cron_mode` |
| `stt` | `enabled`, `provider` (local/groq/openai/mistral/elevenlabs/deepinfra) |
| `tts` | `provider` (edge/elevenlabs/openai/minimax/mistral/neutts/gemini/piper/kittentts/deepinfra/xai) |
| `memory` | `memory_enabled`, `user_profile_enabled`, `memory_char_limit` (2200), `user_char_limit` (1375), `nudge_interval` (unset = review on explicit "remember"/correction events; a value makes it a counter), `write_approval` (false), `provider` |
| `skills` | `creation_nudge_interval` (unset = event-driven; a value makes it a counter) |
| `auxiliary.background_review` | `enabled` (true), `model`, `max_input_tokens` |
| `security` | `redact_secrets`, `tirith_enabled`, `website_blocklist` |
| `delegation` | `model`, `provider`, `max_concurrent_children` (10), `max_iterations` (250), `max_spawn_depth` (1) |
| `checkpoints` | `enabled`, `max_snapshots` (50) |
| `curator` | `enabled`, `consolidate` (false), `prune_builtins` (true), `interval_hours` (168), `stale_after_days` (30), `archive_after_days` (90) |
| `kanban` | `chat_tools` (main), `dispatch_in_gateway`, `dispatch_interval_seconds` (60), `failure_limit` (2) |

`korra config check` reports sections missing from an older config.

### Toolsets

Enable/disable via `korra tools` (interactive) or `korra tools enable/disable NAME`.
Full enumeration: `TOOLSETS` dict in `toolsets.py` (`_HERMES_CORE_TOOLS` is the default bundle most platforms inherit).

| Toolset | What it provides |
|---------|-----------------|
| `web` / `search` | Web search + extraction / search-only subset |
| `browser` | Browser automation (Browserbase, Camofox, or local Chromium) |
| `terminal` | Shell commands and process management |
| `file` | File read/write/search/patch |
| `code_execution` | Sandboxed Python execution |
| `coding` | Code-editing helpers (LSP-backed) |
| `vision` | Image analysis |
| `image_gen` | Image generation and image-to-image editing |
| `video` / `video_gen` | Video analysis / video generation |
| `tts` | Text-to-speech |
| `skills` | Skill browsing and management |
| `memory` | Persistent cross-session memory |
| `session_search` | Search past conversations |
| `context_engine` | Pluggable context-engine hooks |
| `project` | Named multi-folder workspace tools |
| `delegation` | Subagent task delegation |
| `cronjob` | Scheduled task management |
| `clarify` | Ask user clarifying questions |
| `todo` | In-session task planning |
| `kanban` | Multi-agent work-queue tools (gated to workers) |
| `debugging` | Extra introspection tools (off by default) |
| `safe` | Minimal low-risk toolset for locked-down sessions |

Tool changes take effect on `/reset` (new session) — never mid-conversation, to preserve prompt caching.

## Voice

### STT (Voice → Text)

Voice messages from messaging platforms are auto-transcribed.

```yaml
stt:
  enabled: true
  provider: local   # local (faster-whisper, free) | groq | openai | mistral | elevenlabs | deepinfra
  local:
    model: base     # tiny, base, small, medium, large-v3
```

Auto-detect priority: local faster-whisper (`pip install faster-whisper`) → Groq (`GROQ_API_KEY`, free tier) → OpenAI (`VOICE_TOOLS_OPENAI_KEY`) → Mistral Voxtral (`MISTRAL_API_KEY`).

### TTS (Text → Voice)

| Provider | Env var | Free? |
|----------|---------|-------|
| Edge TTS (default) | None | Yes |
| ElevenLabs | `ELEVENLABS_API_KEY` | Free tier |
| OpenAI | `VOICE_TOOLS_OPENAI_KEY` | Paid |
| MiniMax | `MINIMAX_API_KEY` | Paid |
| Mistral | `MISTRAL_API_KEY` | Paid |
| Gemini | `GOOGLE_API_KEY` | Free tier |
| NeuTTS / Piper / KittenTTS (local) | None | Free |

Voice commands: `/voice on` (voice-to-voice), `/voice tts` (always voice), `/voice off`.
