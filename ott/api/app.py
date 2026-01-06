"""FastAPI application instance"""

from fastapi import FastAPI

app = FastAPI(
    title="OTT Hooks",
    description="OTT-aware governance for Radarr/Sonarr",
    version="1.0.0"
)
