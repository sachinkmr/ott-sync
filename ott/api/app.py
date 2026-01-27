"""FastAPI application instance"""

from fastapi import FastAPI
from fastapi.responses import JSONResponse

app = FastAPI(
    title="OTT Hooks",
    description="""
    OTT-aware governance for Radarr/Sonarr with intelligent caching and metrics.
    
    ## Features
    - Real-time webhook processing for movie/series events
    - JustWatch OTT provider detection with database caching
    - Telegram notifications with interactive override buttons
    - Comprehensive metrics and analytics
    - Tag-based item tracking and history
    - Error logging and monitoring
    
    ## Endpoints
    - `/radarr` - Radarr webhook endpoint
    - `/sonarr` - Sonarr webhook endpoint
    - `/telegram/callback` - Telegram button callbacks
    - `/health` - Health check and system status
    - `/metrics` - Processing metrics and statistics
    - `/cache/*` - Cache management endpoints
    """,
    version="2.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)
