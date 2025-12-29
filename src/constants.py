"""Constants used across the application"""

# Tag names for tracking item state
TAG_OTT_SKIPPED = "ott-skipped"
TAG_OTT_PROCESSED = "ott-processed"
TAG_OTT_OVERRIDE = "ott-override"

# Webhook event types
EVENT_MOVIE_ADDED = "MovieAdded"
EVENT_SERIES_ADDED = "SeriesAdded"
EVENT_GRAB = "Grab"

# Supported event types
SUPPORTED_EVENTS = {EVENT_MOVIE_ADDED, EVENT_SERIES_ADDED, EVENT_GRAB}

# Default configuration values
DEFAULT_CRON_INITIAL_DELAY_SECONDS = 60
DEFAULT_CRON_INTERVAL_HOURS = 24
DEFAULT_SERVER_HOST = "0.0.0.0"
DEFAULT_SERVER_PORT = 9123
DEFAULT_REGION = "IN"
DEFAULT_THREAD_POOL_SIZE = 5

# API endpoints
COMMAND_CANCEL_DOWNLOADS = "CancelPendingDownloads"
COMMAND_MOVIES_SEARCH = "MoviesSearch"
COMMAND_SERIES_SEARCH = "SeriesSearch"
