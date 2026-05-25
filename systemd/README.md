# Systemd Services

Sammurai systemd user services for automated operation.

## Services

### sammurai-agent.service

Agent daemon that monitors WhatsApp for queries and responds via Hermes.

**Status:**
```bash
systemctl --user status sammurai-agent.service
```

**Logs:**
```bash
journalctl --user -u sammurai-agent.service -f
```

**Config:**
- Brain chat JID: configured in `config.yaml` under `agent.brain_chat_jid`
- Poll interval: `agent.poll_interval` (default: 1800s / 30min)
- Environment: `~/.config/sammurai/env`

**Note:** This service provides standalone agent functionality. Currently, Nous Research Hermes is used instead via skill integration (see `integrations/hermes/`).

### sammurai-digest.timer

Daily timer that runs digest extraction at 23:59 UTC.

**Status:**
```bash
systemctl --user status sammurai-digest.timer
systemctl --user list-timers sammurai-digest.timer
```

**What it does:**
1. Extracts messages from configured groups (last 1 day)
2. Generates digest with topics + action items
3. Compiles wiki markdown files
4. Rebuilds cognee RAG index
5. Git commits changes to `~/sammurai-brain/`

**Config:**
- Groups: `parallel.groups` in `config.yaml`
- Days lookback: `cron.days` (default: 1)
- Override groups: `cron.groups` (empty = use parallel.groups)

**Trigger manually:**
```bash
systemctl --user start sammurai-digest.service
```

Or run directly:
```bash
cd ~/ai/sammurai
~/.venv/bin/python -m src.backend.digest_runner
```

## Installation

Services should already be installed at:
- `~/.config/systemd/user/sammurai-agent.service`
- `~/.config/systemd/user/sammurai-digest.service`
- `~/.config/systemd/user/sammurai-digest.timer`

If missing, copy from this directory:

```bash
cp systemd/*.service ~/.config/systemd/user/
cp systemd/*.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable sammurai-digest.timer
systemctl --user start sammurai-digest.timer
```

## Environment Variables

Create `~/.config/sammurai/env`:

```bash
mkdir -p ~/.config/sammurai
cat > ~/.config/sammurai/env << 'EOF'
LITELLM_BASE_URL=https://your-litellm-url.com
LITELLM_API_KEY=your-api-key
EMBEDDING_ENDPOINT=https://your-litellm-url.com
EOF
```

## Logs

All services log to systemd journal:

```bash
# Agent logs
journalctl --user -u sammurai-agent.service -f

# Digest logs
journalctl --user -u sammurai-digest.service -f

# Timer logs
journalctl --user -u sammurai-digest.timer -f
```

## Troubleshooting

### Service fails to start

Check environment file exists:
```bash
cat ~/.config/sammurai/env
```

Check venv exists:
```bash
ls ~/.venv/bin/python
```

### Digest timer not running

```bash
systemctl --user list-timers sammurai-digest.timer
```

If inactive:
```bash
systemctl --user enable sammurai-digest.timer
systemctl --user start sammurai-digest.timer
```

### Check last run

```bash
systemctl --user status sammurai-digest.service
```

Shows last execution time and exit code.

## Disable Services

Prefer Hermes integration over standalone agent:

```bash
# Stop and disable standalone agent
systemctl --user stop sammurai-agent.service
systemctl --user disable sammurai-agent.service

# Keep digest timer active
systemctl --user status sammurai-digest.timer
```
