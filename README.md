# OTT Hooks - OTT-Aware Governance for Radarr/Sonarr

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.127.0-green.svg)](https://fastapi.tiangolo.com/)
[![Docker](https://img.shields.io/badge/docker-ready-brightgreen.svg)](https://www.docker.com/)
[![Version](https://img.shields.io/badge/version-2.0.0-blue.svg)](https://github.com/yourusername/ott-sync/blob/main/CHANGELOG.md)

**Intelligent OTT awareness for your *arr stack** - automatically prevent downloads for content already available on your streaming services.

## 🎯 What It Does

OTT Hooks integrates with Radarr/Sonarr to:

- ✅ **Detect OTT availability** using JustWatch API
- ✅ **Block downloads** for content on Netflix, Prime, Disney+, etc.
- ✅ **Send Telegram alerts** with override buttons
- ✅ **Human override capability** via interactive buttons
- ✅ **Provider tagging** - Tag items with specific platforms (ott-netflix, ott-prime-video, etc.)
- ✅ **Scheduled reconciliation** to catch missed items
- ✅ **Hot reload config** without container restart
- ✅ **Migration tools** to retroactively tag existing libraries

## 🆕 What's New in v2.0.0

### 📊 Database Integration
- **SQLite database** for persistent metrics, cache, and history
- **Historical metrics** - Track processing stats over time
- **JustWatch cache** - 7-day caching reduces API calls by ~80%
- **Override history** - Full audit trail of user actions
- **Tag history** - Track all tag changes with timestamps

### 🚀 Performance Improvements
- **Smart caching** - JustWatch results cached with intelligent TTL
- **Hit rate tracking** - Monitor cache performance
- **Reduced API calls** - Save on rate limits and improve speed
- **Database indexes** - Optimized queries for large libraries

### 📈 Analytics & Monitoring
- **Comprehensive metrics** - Hourly, daily, weekly breakdowns
- **Error logging** - Structured error tracking with severity levels
- **Health monitoring** - Database, services, and system status
- **Cache statistics** - Real-time hit/miss rates

### 🔧 Enhanced API
- **Interactive Swagger docs** at `/docs`
- **Metrics endpoints** - `/metrics`, `/metrics/hourly`, `/errors`
- **Cache management** - `/cache/stats`, `/cache/invalidate`, `/cache/cleanup`
- **Better health checks** - Detailed component status
- **Request/response models** - Full Pydantic validation

### 🎨 Configuration Improvements
- **Backward compatible** - Existing configs work without changes
- **New options** - Database, cache, performance tuning
- **Detailed comments** - Comprehensive config.json.example
- **Sensible defaults** - Works out of the box

## 📋 Features

### Event-Driven Architecture
- Webhook endpoints for real-time processing
- Handles `MovieAdded`, `SeriesAdded`, and `Grab` events
- Instant blocking before downloads start

### Smart Enforcement
- Cancels pending downloads
- Unmonitors blocked items
- Deletes downloaded files (saves disk space)
- Tags items with OTT providers (ott-netflix, ott-prime-video, etc.)
- Tags items for tracking (`ott-skipped`, `ott-processed`, `ott-override`)

### Human Control
- Telegram notifications with poster images
- One-click override buttons
- Re-blocking detection (prevents bypass)
- User-friendly status messages

### Manual Approval Mode (`auto_download: false`)
- **Every** new Radarr/Sonarr addition is held for explicit approval
- Item is unmonitored and any queued download is cancelled on arrival
- Notification shows TMDB + AniList ratings (when available) to inform the decision
- Single "✅ Approve Download" button re-monitors the item and triggers a search
- No timeout — approvals wait indefinitely
- Apply the `ott-override` tag manually to bypass the pipeline entirely
  (useful for anything you've already decided you want)

### Production Ready
- Modular architecture (20+ Python modules)
- Type hints throughout
- Comprehensive logging
- Docker containerization
- Non-root user for security
- Health check endpoints
- Configuration hot reload
- **NEW**: SQLite database with WAL mode
- **NEW**: Intelligent caching system
- **NEW**: Metrics and analytics

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
  "cron_interval_hours": 24,
  "auto_download": false
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
4. **Register webhook** (Required for callbacks to work):
   ```bash
   curl -X POST "https://api.telegram.org/bot<YOUR_BOT_TOKEN>/setWebhook" \
     -H "Content-Type: application/json" \
     -d '{
       "url": "https://your-domain.com:9123/telegram/callback",
       "allowed_updates": ["callback_query"]
     }'
   ```
   **Note**: Your server must be publicly accessible via HTTPS for callbacks to work.

5. Add credentials to `config.json`

**Verify webhook status:**
```bash
curl "https://api.telegram.org/bot<YOUR_TOKEN>/getWebhookInfo"
```

## 📡 API Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/health` | GET | Health check for monitoring |
| `/radarr` | POST | Webhook from Radarr |
| `/sonarr` | POST | Webhook from Sonarr |
| `/telegram/callback` | POST | Telegram callback handler (override buttons) |
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

## 🎮 Usage

### CLI Commands

```bash
# Run webhook server + scheduled cron (production)
python main.py run-all --host 0.0.0.0 --port 9123

# Run webhook server only
python main.py server --host 0.0.0.0 --port 9123

# Run cron cleanup once
python main.py cron

# Migrate existing libraries (add OTT provider tags)
python main.py migrate-ott-tags radarr           # Radarr only
python main.py migrate-ott-tags sonarr           # Sonarr only
python main.py migrate-ott-tags all              # Both services
python main.py migrate-ott-tags all --unmonitor  # Also unmonitor OTT items
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

## � Migrating Existing Libraries

### One-Time Migration

Retroactively tag all existing items with OTT provider tags:

```bash
# Tag all movies and series (recommended)
docker exec ott-hooks python main.py migrate-ott-tags all

# Tag Radarr movies only
docker exec ott-hooks python main.py migrate-ott-tags radarr

# Tag Sonarr series only
docker exec ott-hooks python main.py migrate-ott-tags sonarr

# Also unmonitor items found on OTT
docker exec ott-hooks python main.py migrate-ott-tags all --unmonitor
```

### What Migration Does

1. ✅ Scans all items in your library
2. ✅ Looks up OTT availability via JustWatch
3. ✅ Adds provider tags (ott-netflix, ott-prime-video, etc.)
4. ✅ Adds `ott-skipped` tag for blocked items
5. ✅ Adds `ott-processed` tag for all checked items
6. ✅ Optionally unmonitors items with `--unmonitor` flag
7. ✅ Skips items with `ott-override` tag
8. ✅ Progress logging every 50 items

### Performance Notes

- ⏱️ **Time**: ~16-20 minutes for 1000 items (respects JustWatch rate limits)
- 🔄 **Rate Limiting**: 60 API calls per 60 seconds
- 🔒 **Safe**: Can run during normal operation
- 📊 **Statistics**: Provides detailed report when complete

### Migration Output Example

```
============================================================
Starting OTT provider tag migration
📌 TAG-ONLY MODE: Monitoring status will NOT be changed
============================================================

🎬 Migrating Radarr movies...
[OTT-MIGRATE] Processing 1253 items...
[OTT-MIGRATE] Progress: 50/1253 items processed
[OTT-MIGRATE] The Batman found on: Netflix, Prime Video
[OTT-MIGRATE] Progress: 100/1253 items processed
...
[OTT-MIGRATE] Migration complete: {
  'total': 1253,
  'tagged': 456,
  'not_on_ott': 650,
  'already_tagged': 120,
  'errors': 5,
  'skipped_override': 22
}
✅ Radarr migration complete

============================================================
OTT provider tag migration completed!
============================================================
```

## �📝 Tags

### System Tags

| Tag | Purpose | Applied When |
|-----|---------|--------------||
| `ott-skipped` | Item blocked due to OTT | Found on streaming service |
| `ott-processed` | Item already checked | After JustWatch lookup |
| `ott-override` | User approved download | Telegram override button |

### Provider Tags

Items found on OTT platforms are automatically tagged with provider-specific tags:

| Provider | Tag Created |
|----------|-------------|
| Netflix | `ott-netflix` |
| Prime Video | `ott-prime-video` |
| Disney Plus Hotstar | `ott-disney-plus-hotstar` |
| Apple TV+ | `ott-apple-tv` |
| HBO Max | `ott-hbo-max` |
| And more... | `ott-{provider-name}` |

**Benefits:**
- 📊 Track which platforms have your content
- 🔍 Filter/search by provider in Radarr/Sonarr
- 📈 Analytics on OTT distribution
- 🏷️ Historical record (tags kept even after override)

**Tag Behavior:**
- **Webhooks**: Add all provider tags when item is found on OTT
- **Cron**: Update tags (add new, remove stale)
- **Override**: Provider tags are kept for tracking
- **Not on OTT**: No provider tags added

### Tag Combinations

**Item on Netflix (blocked):**
```
Tags: ott-netflix, ott-skipped, ott-processed
```

**User approves download:**
```
Tags: ott-netflix, ott-override, ott-processed
      ↑ Provider tag kept for tracking
```

**Item not on any OTT:**
```
Tags: ott-processed
```

## 🐛 Troubleshooting

### "Config file not found"
Ensure `/config/config.json` exists in container:
```bash
docker exec ott-hooks ls -la /config/
```

### "Telegram callbacks not working"
1. Verify webhook is registered:
   ```bash
   curl "https://api.telegram.org/bot<TOKEN>/getWebhookInfo"
   ```
2. Check for errors in webhook info (502 = server not accessible)
3. Ensure server is publicly accessible via HTTPS
4. Verify port 9123 is exposed: `docker ps | grep ott-hooks`
5. Test endpoint locally: `curl http://localhost:9123/health`

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

### "Migration taking too long"
- Expected: ~1 minute per 50-60 items (JustWatch rate limits)
- Safe to interrupt with Ctrl+C and resume later
- Already-tagged items are skipped on re-run

## 🐛 Troubleshooting

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
