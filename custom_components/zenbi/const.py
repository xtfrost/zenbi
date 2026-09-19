"""Constants for the Zenbi integration."""

DOMAIN = "zenbi"

# Configuration keys
CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_UNIQUE_DEVICE_ID = "unique_device_id"
CONF_CALENDAR_SYNC_INTERVAL_HOURS = "calendar_sync_interval_hours"
CONF_NOTIFICATION_SYNC_INTERVAL_MINS = "notification_sync_interval_mins"

# Default values
DEFAULT_CALENDAR_SYNC_INTERVAL_HOURS = 24
DEFAULT_NOTIFICATION_SYNC_INTERVAL_MINS = 15

# API Defaults
BASE_URL = "https://app.zenbi.dk"
TWO_FACTOR_NULL_GUID = "00000000-0000-0000-0000-000000000000"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

# Endpoints
ENDPOINT_AUTHENTICATE = "/api/authentication/api/v1/account/authenticate"
ENDPOINT_GLOBALDATA = "/api/authentication/api/v1/globaldata"
ENDPOINT_CALENDAR_ITEMS = "/api/calendar/api/v1/calendaritems/own/relation/users"
ENDPOINT_PLANNING_LABELS = "/api/planning/api/v1/home/label"
ENDPOINT_HOMEWORKS = "/api/calendar/api/v1/homeworks/relations"
ENDPOINT_WEEKLY_SCHEDULES = "/api/homework/api/v1/weeklyschedules/relations"

# Platforms
PLATFORMS = ["calendar"]
