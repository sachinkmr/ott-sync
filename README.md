# OTT Hooks - OTT-Aware Governance for Radarr/Sonarr

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.127.0-green.svg)](https://fastapi.tiangolo.com/)
[![Docker](https://img.shields.io/badge/docker-ready-brightgreen.svg)](https://www.docker.com/)

**Intelligent OTT awareness for your *arr stack** - automatically prevent downloads for content already available on your streaming services.

## 🎯 What It Does

OTT Hooks integrates with Radarr/Sonarr to:

- ✅ **Detect OTT availability** using JustWatch API
- ✅ **Block downloads** for content on Netflix, Prime, Disney+, etc.
- ✅ **Send Telegram alerts** with override buttons
- ✅ **Human override capability** via interactive buttons
- ✅ **Scheduled reconciliation** to catch missed items
- ✅ **Hot reload config** without container restart

## 📋 Features

### Event-Driven Architecture
- Webhook endpoints for real-time processing
- Handles `MovieAdded`, `SeriesAdded`, and `Grab` events
- Instant blocking before downloads start

### Smart Enforcement
- Cancels pending downloads
- Unmonitors blocked items
- Deletes downloaded files (saves disk space)
- Tags items for tracking (`ott-skipped`, `ott-processed`, `ott-override`)

### Human Control
- Telegram notifications with poster images
- One-click override buttons
- Re-blocking detection (prevents bypass)
- User-friendly status messages

### Production Ready
- Modular architecture (15+ Python modules)
- Type hints throughout
- Comprehensive logging
- Docker containerization
- Non-root user for security
- Health check endpoints
- Configuration hot reload

## 🚀 Quick Start

### 1. Create Configuration

```bash
cp config.json.example config.json
```

Edit `config.json` with your details:

```json
{
  "radarr_url": "http://radarr:7878",
  "radarr_api_key": "your_radarr_api_key",
  "sonarr_url": "http://sonarr:8989",
  "sonarr_api_key": "your_sonarr_api_key",
  "ott_providers": ["Netflix", "Prime Video", "Disney Plus Hotstar"],
  "telegram": {
    "enabled": true,
    "bot_token": "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11",
    "chat_id": "-1001234567890",
    "region": "India"
  },
  "region": "IN",
  "cron_initial_delay_seconds": 60,
  "cron_interval_hours": 24
}
```

### 2. Docker Compose

```yaml
version: '3.8'

services:
  ott-hooks:
    build: .
    container_name: ott-hooks
    restart: unless-stopped
    volumes:
      - /path/to/config:/config
    ports:
      - "9123:9123"
    environment:
      - TZ=Asia/Kolkata
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:9123/health"]
      interval: 30s
      timeout: 10s
      retries: 3
```

### 3. Configure Webhooks

#### Radarr
1. Settings → Connect → Add Webhook
2. Name: `OTT Hooks - Radarr`
3. URL: `http://ott-hooks:9123/radarr`
4. Method: `POST`
5. Triggers: ✅ On Import, ✅ On Grab, ✅ On Movie Added

#### Sonarr
1. Settings → Connect → Add Webhook
2. Name: `OTT Hooks - Sonarr`
3. URL: `http://ott-hooks:9123/sonarr`
4. Method: `POST`
5. Triggers: ✅ On Import, ✅ On Grab, ✅ On Series Added

### 4. Telegram Bot Setup

1. Create bot via [@BotFather](https://t.me/botfather)
2. Get bot token (format: `123456:ABC-DEF...`)
3. Get chat ID:
   ```bash
   # Start chat with bot, then:
   curl https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates
   ```
4. Add credentials to `config.json`

## 🏗️ Architecture

```
ott-sync/
├── main.py                   # Entry point with hot reload
└── src/
    ├── config.py            # Configuration management
    ├── constants.py         # Tag names, event types
    ├── models.py            # Data classes
    ├── clients/             # External API clients
    │   ├── telegram.py      # Telegram Bot API
    │   ├── justwatch.py     # OTT provider lookup
    │   └── arr_client.py    # Radarr/Sonarr HTTP client
    ├── managers/            # Business logic
    │   ├── base.py          # Core OTT governance
    │   ├── radarr.py        # Radarr-specific
    │   └── sonarr.py        # Sonarr-specific
    ├── api/                 # FastAPI routes
    │   ├── webhooks.py      # /radarr, /sonarr
    │   ├── telegram.py      # /telegram/callback
    │   └── health.py        # /health, /cron
    ├── cli/                 # CLI commands
    │   └── commands.py      # cron, server, run-all
    └── utils/               # Utilities
        └── reload.py        # Hot reload functionality

```

## 📡 API Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/health` | GET | Health check for monitoring |
| `/radarr` | POST | Webhook from Radarr |
| `/sonarr` | POST | Webhook from Sonarr |
| `/telegram/callback` | POST | Override button handler |
| `/cron` | GET | Manual cron trigger |
| `/docs` | GET | OpenAPI documentation |

## 🔧 Configuration

### OTT Providers

Supported providers (case-sensitive):
- `Netflix`
- `Prime Video`
- `Disney Plus Hotstar`
- `Apple TV Plus`
- `Hulu`
- And 100+ more via JustWatch

### Region Codes

Use ISO 3166-1 alpha-2 codes:
- `IN` - India
- `US` - United States
- `GB` - United Kingdom
- `CA` - Canada
- `AU` - Australia

### Hot Reload

Change config without restarting:

**Method 1: File modification** (automatic after 30s)
```bash
vim /path/to/config/config.json
# Changes detected and applied automatically
```

**Method 2: SIGHUP signal** (immediate)
```bash
docker kill -s HUP ott-hooks
```

See [CONFIG_RELOAD.md](CONFIG_RELOAD.md) for details.

## 🎮 Usage

### CLI Commands

```bash
# Run webhook server + scheduled cron (production)
python main.py run-all --host 0.0.0.0 --port 9123

# Run webhook server only
python main.py server --host 0.0.0.0 --port 9123

# Run cron cleanup once
python main.py cron --service radarr
python main.py cron --service sonarr
python main.py cron --service all
```

### Docker

```bash
# Build image
docker build -t ott-hooks .

# Run container
docker run -d \
  --name ott-hooks \
  -v /path/to/config:/config \
  -p 9123:9123 \
  ott-hooks

# View logs
docker logs -f ott-hooks

# Reload config
docker kill -s HUP ott-hooks
```

## 📊 Workflow

### 1. Movie/Series Added
```
User adds movie in Radarr
    ↓
Radarr sends webhook → /radarr
    ↓
OTT Hooks checks JustWatch API
    ↓
╔════════════════╗    ╔════════════════╗
║ Found on OTT?  ║    ║ Not on OTT?    ║
╚════════════════╝    ╚════════════════╝
        ↓                      ↓
Cancel downloads         Allow download
Unmonitor item          Mark processed
Delete files            Continue normally
Send Telegram alert
Tag: ott-skipped
```

### 2. Telegram Override
```
User clicks "Download anyway"
    ↓
Telegram sends callback → /telegram/callback
    ↓
OTT Hooks processes override
    ↓
Remove ott-skipped tag
Add ott-override tag
Set monitored = true
Trigger search
    ↓
Radarr downloads content
```

### 3. Scheduled Cron
```
Every 24 hours (configurable)
    ↓
Fetch all monitored items
    ↓
For each item:
  - Check OTT availability
  - Apply same logic as webhooks
  - Catch items added outside webhooks
```

## 📝 Tags

| Tag | Purpose | Applied When |
|-----|---------|--------------|
| `ott-skipped` | Item blocked due to OTT | Found on streaming service |
| `ott-processed` | Item already checked | After JustWatch lookup |
| `ott-override` | User approved download | Telegram override button |

## 🐛 Troubleshooting

### "Config file not found"
Ensure `/config/config.json` exists in container:
```bash
docker exec ott-hooks ls -la /config/
```

### "Telegram not sending notifications"
Check bot token and chat ID:
```bash
curl https://api.telegram.org/bot<TOKEN>/getMe
```

### "Radarr/Sonarr connection failed"
Verify network and API key:
```bash
docker exec ott-hooks curl http://radarr:7878/api/v3/system/status -H "X-Api-Key: YOUR_KEY"
```

### "JustWatch lookup failed"
Check internet connectivity and region code:
```bash
docker exec ott-hooks curl https://apis.justwatch.com/graphql
```


## 🤝 Contributing

Contributions welcome! Areas for improvement:
- [ ] Multi-region support
- [ ] Performance optimizations

## 📄 License

MIT License - See LICENSE file

## 🙏 Acknowledgments

- [JustWatch](https://www.justwatch.com/) for OTT availability data
- [Radarr](https://radarr.video/) & [Sonarr](https://sonarr.tv/) teams
- [FastAPI](https://fastapi.tiangolo.com/) framework
- [Telegram Bot API](https://core.telegram.org/bots/api)

---

**Made with ❤️ for the *arr community**
