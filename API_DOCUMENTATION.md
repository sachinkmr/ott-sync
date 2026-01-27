# API Documentation

Complete API reference for OTT Hooks v2.0.0

## Base URL

```
http://localhost:9123
```

## Interactive Documentation

- **Swagger UI**: `http://localhost:9123/docs`
- **ReDoc**: `http://localhost:9123/redoc`

## Webhooks

### POST /radarr

Processes Radarr webhook events.

**Supported Events:**
- `MovieAdded` - When a new movie is added to Radarr
- `Grab` - When a movie download is grabbed

**Request Body:**
```json
{
  "eventType": "MovieAdded",
  "movie": {
    "id": 123,
    "title": "Inception",
    "year": 2010,
    "tmdbId": 27205,
    "imdbId": "tt1375666",
    "tags": [1, 2, 3],
    "monitored": true
  }
}
```

**Response:**
```json
{
  "ok": true,
  "message": "Processed successfully",
  "item_id": 123,
  "action": "blocked"
}
```

### POST /sonarr

Processes Sonarr webhook events.

**Supported Events:**
- `SeriesAdded` - When a new series is added to Sonarr
- `Grab` - When a series episode download is grabbed

**Request Body:**
```json
{
  "eventType": "SeriesAdded",
  "series": {
    "id": 456,
    "title": "Breaking Bad",
    "year": 2008,
    "tvdbId": 81189,
    "imdbId": "tt0903747",
    "tags": [1, 2],
    "monitored": true
  }
}
```

**Response:**
```json
{
  "ok": true,
  "message": "Processed successfully",
  "item_id": 456,
  "action": "allowed"
}
```

## System Endpoints

### GET /health

Comprehensive health check including database and service status.

**Response:**
```json
{
  "status": "healthy",
  "timestamp": "2026-01-27T10:30:00Z",
  "version": "2.0.0",
  "uptime_seconds": 86400,
  "database": {
    "status": "healthy",
    "path": "/config/ott-hooks.db",
    "size_mb": 15.3,
    "wal_enabled": true,
    "tables": {
      "processing_metrics": 1500,
      "justwatch_cache": 3200,
      "override_history": 45,
      "tag_history": 890,
      "webhook_log": 2100,
      "error_log": 12
    }
  },
  "services": {
    "radarr": "healthy",
    "sonarr": "healthy"
  }
}
```

### GET /ping

Simple connectivity check.

**Response:**
```json
{
  "ok": true
}
```

## Metrics Endpoints

### GET /metrics

Get aggregated processing statistics.

**Query Parameters:**
- `service` (optional): Filter by `radarr` or `sonarr`
- `hours` (optional, default: 24): Time period in hours

**Examples:**
```bash
# Last 24 hours, all services
curl "http://localhost:9123/metrics"

# Last 7 days, Radarr only
curl "http://localhost:9123/metrics?service=radarr&hours=168"
```

**Response:**
```json
{
  "period_hours": 24,
  "service": "all",
  "total_checked": 150,
  "total_cleaned": 45,
  "total_marked_processed": 105,
  "total_skipped_override": 5,
  "total_already_processed": 200,
  "total_errors": 2,
  "total_items_processed": 355,
  "total_operations": 8
}
```

### GET /metrics/hourly

Get hourly breakdown of processing metrics.

**Query Parameters:**
- `service` (optional): Filter by `radarr` or `sonarr`
- `hours` (optional, default: 24): Number of hours to include

**Response:**
```json
[
  {
    "hour": "2026-01-27 09:00:00",
    "items_processed": 15,
    "errors": 0,
    "operations": 2
  },
  {
    "hour": "2026-01-27 10:00:00",
    "items_processed": 23,
    "errors": 1,
    "operations": 3
  }
]
```

### GET /errors

Get recent error logs.

**Query Parameters:**
- `limit` (optional, default: 50): Maximum number of errors to return
- `unresolved_only` (optional, default: false): Only show unresolved errors

**Example:**
```bash
curl "http://localhost:9123/errors?unresolved_only=true&limit=20"
```

**Response:**
```json
[
  {
    "id": 45,
    "timestamp": "2026-01-27T10:25:00Z",
    "error_type": "JustWatchAPIError",
    "severity": "error",
    "service": "radarr",
    "item_id": 123,
    "error_message": "Failed to lookup providers: Connection timeout",
    "operation": "justwatch_lookup",
    "resolved": false
  }
]
```

## Cache Management Endpoints

**Note:** All cache endpoints are restricted to localhost and local network (192.168.x.x, 10.x.x.x) for security.

### GET /cache/stats

Get JustWatch cache statistics.

**Response:**
```json
{
  "total_entries": 3200,
  "expired_entries": 150,
  "active_entries": 3050,
  "cache_hits": 12500,
  "cache_misses": 2800,
  "hit_rate_percent": 81.70
}
```

### POST /cache/invalidate

Invalidate specific cache entries or clear entire cache.

**Query Parameters:**
- `tmdb_id` (optional): Invalidate by TMDB ID
- `title` (optional): Invalidate by title
- `year` (optional): Used with title for specificity

**Examples:**
```bash
# Invalidate specific movie by TMDB ID
curl -X POST "http://localhost:9123/cache/invalidate?tmdb_id=550"

# Invalidate by title and year
curl -X POST "http://localhost:9123/cache/invalidate?title=Inception&year=2010"

# Clear entire cache (use with caution!)
curl -X POST "http://localhost:9123/cache/invalidate"
```

**Response:**
```json
{
  "ok": true,
  "deleted": 1,
  "message": "Invalidated 1 cache entries"
}
```

### POST /cache/cleanup

Remove expired cache entries.

**Example:**
```bash
curl -X POST "http://localhost:9123/cache/cleanup"
```

**Response:**
```json
{
  "ok": true,
  "deleted": 150,
  "message": "Cleaned up 150 expired cache entries"
}
```

## Utility Endpoints

### GET /cron

Manually trigger cron cleanup for both services.

**Response:**
```json
{
  "ok": true,
  "radarr": {
    "checked": 1008,
    "cleaned": 15,
    "marked_processed": 980,
    "skipped_override": 3,
    "already_processed": 500,
    "errors": 0
  },
  "sonarr": {
    "checked": 608,
    "cleaned": 8,
    "marked_processed": 590,
    "skipped_override": 2,
    "already_processed": 300,
    "errors": 0
  }
}
```

### GET /wakeup

Send Wake-on-LAN magic packet (custom utility).

**Response:**
```json
{
  "status": "ok",
  "message": "Magic packet sent successfully"
}
```

## Error Responses

All endpoints may return standard HTTP error responses:

**400 Bad Request:**
```json
{
  "detail": "Invalid request parameters"
}
```

**403 Forbidden:**
```json
{
  "detail": "Cache invalidation is restricted to localhost and local network only"
}
```

**500 Internal Server Error:**
```json
{
  "detail": "Database connection failed"
}
```

## Rate Limiting

JustWatch API calls are rate limited per configuration:
- Default: 60 calls per 60 seconds
- Configurable via `justwatch_rate_limit` in config.json

## Webhook Setup

### Radarr Configuration

1. Settings → Connect → Add Webhook
2. **Name:** `OTT Hooks - Radarr`
3. **URL:** `http://ott-hooks:9123/radarr`
4. **Method:** `POST`
5. **Events:** Select `On Movie Added` and `On Grab`

### Sonarr Configuration

1. Settings → Connect → Add Webhook
2. **Name:** `OTT Hooks - Sonarr`
3. **URL:** `http://ott-hooks:9123/sonarr`
4. **Method:** `POST`
5. **Events:** Select `On Series Added` and `On Grab`

## Testing

### Test Webhook Locally

```bash
# Test Radarr webhook
curl -X POST http://localhost:9123/radarr \
  -H "Content-Type: application/json" \
  -d '{
    "eventType": "MovieAdded",
    "movie": {
      "id": 999,
      "title": "Test Movie",
      "year": 2024,
      "tmdbId": 550,
      "monitored": true
    }
  }'

# Test Sonarr webhook
curl -X POST http://localhost:9123/sonarr \
  -H "Content-Type: application/json" \
  -d '{
    "eventType": "SeriesAdded",
    "series": {
      "id": 888,
      "title": "Test Series",
      "year": 2024,
      "tvdbId": 12345,
      "monitored": true
    }
  }'
```

### Monitor Health

```bash
# Check system health
curl http://localhost:9123/health | jq

# Check cache statistics
curl http://localhost:9123/cache/stats | jq

# View recent metrics
curl http://localhost:9123/metrics | jq
```
