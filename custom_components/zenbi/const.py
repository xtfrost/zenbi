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
ENDPOINT_WEEKLY_SCHEDULE_FILE_DOWNLOAD = "/api/homework/api/v1/weeklyschedule/filedownload"

# Platforms
PLATFORMS = ["calendar", "todo", "sensor"]

# Storage constants
# IMPORTANT: Do NOT increment STORAGE_VERSION without also updating async_remove_entry
# in __init__.py and adding a data-migration path in todo.py. The Store key used in
# async_remove_entry must always match the version written by the todo platform,
# otherwise the .storage file will not be deleted on uninstallation.
STORAGE_VERSION = 1
STORAGE_KEY_TODO = f"{DOMAIN}.{{entry_id}}"


def slugify_name(name: str) -> str:
    """Create a clean, lowercased slug from a name for unique IDs and identifiers."""
    return "".join(c if c.isalnum() else "_" for c in name.lower()).strip("_")

