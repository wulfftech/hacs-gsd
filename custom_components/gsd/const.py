"""Constants for the GSD integration."""
DOMAIN = "gsd"
TODO_STORAGE_KEY = f"{DOMAIN}_items"

CONF_URL = "url"
CONF_TOKEN = "token"
CONF_SHOW_DUE_IN = "show_due_in"
CONF_CREATE_UNIFIED_LIST = "create_unified_list"
CONF_CREATE_ASSIGNEE_LISTS = "create_assignee_lists"
CONF_REFRESH_INTERVAL = "refresh_interval"

DEFAULT_REFRESH_INTERVAL = 900 # seconds - 15 minutes

API_TIMEOUT = 10  # seconds

# Checkpoints ("baseline done") and pending approvals are polled on their own
# fast interval, independent of the configured chore refresh interval, so
# automations that wait on them react within half a minute.
CHECKPOINT_REFRESH_INTERVAL = 30  # seconds
# Poll rate once the server has said (404) it has neither endpoint, so an older
# server is not hit every 30 seconds for nothing. An upgrade is noticed within this.
CHECKPOINT_UNSUPPORTED_REFRESH_INTERVAL = 3600  # seconds
CHECKPOINTS = ("morning", "afternoon", "evening")
