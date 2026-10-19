"""Global configuration and constants for One User, One Agent."""
import os
from datetime import timedelta, timezone

# Timezones
UTC = timezone.utc
IST = timezone(timedelta(hours=5, minutes=30))

# Constraints & Budgets
MAX_DECISION_LATENCY_MS = 60_000   # 60 seconds strict SLA
FAILURE_LATENCY_MS = 300_000        # 5 minutes failure threshold
MODEL_CALL_TIMEOUT_S = 10           # Per model call timeout
MAX_MODEL_CALLS_PER_DECISION = 8
MAX_TOOL_CALLS_PER_DECISION = 20

# Messaging Policy Constraints
QUIET_HOURS_START_IST = 22  # 22:00 IST
QUIET_HOURS_END_IST = 8     # 08:00 IST
MAX_MESSAGES_PER_IST_DAY = 1  # Push + WhatsApp combined per user per calendar day

# Home Page Constraints
HOME_PAGE_TTL_MINUTES = 120
HOME_PAGE_MAX_ROWS = 6
HOME_PAGE_MIN_TITLES_PER_ROW = 3
HOME_PAGE_MAX_TITLES_PER_ROW = 10
HOME_PAGE_MAX_HERO = 1

# Push Policy Constraints
PUSH_TITLE_MAX_CHARS = 40
PUSH_BODY_MAX_CHARS = 120
PUSH_FORBIDDEN_PHRASES = [
    "last chance",
    "hurry",
    "only today",
    "free forever",
    "guaranteed"
]

# Offline Buffering Threshold
BUFFERED_EVENT_THRESHOLD_SECONDS = 600  # 10 minutes

# WhatsApp Approved Templates
APPROVED_WA_TEMPLATES = {
    "wa_new_episode_v2",
    "wa_recommend_title_v1",
    "wa_trial_ending_v1"
}

# Pricing Plans
VALID_PLANS = {
    "P_TRIAL": {"price_inr": 1, "days": 3, "renews_to": "P_MONTH"},
    "P_MONTH": {"price_inr": 149, "days": 30},
    "P_QUARTER": {"price_inr": 399, "days": 90}
}
VALID_PRICES_INR = {1, 149, 399}
