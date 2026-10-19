"""Fact resolver implementing explicit precedence, freshness, and conflict tracking."""
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from src.config import BUFFERED_EVENT_THRESHOLD_SECONDS, UTC
from src.store.viewer_state import ViewerState


class FactResolver:
    @staticmethod
    def resolve_facts(state: ViewerState, trigger_event: dict, now: datetime) -> Dict[str, Any]:
        """Resolves viewer facts, calculating freshness and tracking conflicts."""
        # Check event age / buffering
        r_at_str = trigger_event.get("received_at")
        o_at_str = trigger_event.get("occurred_at")
        s_at_str = trigger_event.get("sent_at")

        r_at = datetime.fromisoformat(r_at_str.replace("Z", "+00:00")) if r_at_str else now
        o_at = datetime.fromisoformat(o_at_str.replace("Z", "+00:00")) if o_at_str else r_at
        s_at = datetime.fromisoformat(s_at_str.replace("Z", "+00:00")) if s_at_str else r_at

        phone_buffer_delay = max(0.0, (s_at - o_at).total_seconds())
        event_age_s = max(0.0, (r_at - o_at).total_seconds())
        is_buffered = phone_buffer_delay > BUFFERED_EVENT_THRESHOLD_SECONDS or event_age_s > BUFFERED_EVENT_THRESHOLD_SECONDS

        resolved_dialect = state.get_resolved_dialect()
        
        # Dialect conflict formatting for decision schema:
        # e.g., ["profile:d1"] or ["campaign:d2", "profile:d1"]
        dialect_conflicts = []
        if state.profile_dialect and state.profile_dialect != resolved_dialect:
            dialect_conflicts.append(f"profile:{state.profile_dialect}")
        if state.campaign_dialect and state.campaign_dialect != resolved_dialect:
            dialect_conflicts.append(f"campaign_deeplink:{state.campaign_dialect}")

        facts_summary = {
            "viewer_id": state.user_id,
            "arm": state.arm,
            "subscription_status": state.subscription_status,
            "subscription_source": state.facts.get("subscription_status", {}).get("source", "default"),
            "resolved_dialect": resolved_dialect,
            "dialect_source": "app_setting" if state.app_dialect else ("campaign_link" if state.campaign_dialect else "profile_snapshot"),
            "dialect_conflicts": dialect_conflicts,
            "signup_at": state.facts.get("signup_at", {}).get("value"),
            "signup_source": state.facts.get("signup_at", {}).get("source", "unknown"),
            "notifications_opt_in": state.notifications_opt_in,
            "whatsapp_opt_in": state.whatsapp_opt_in,
            "total_plays": state.total_plays,
            "total_completions": state.total_completions,
            "days_since_last_play": (now - state.last_play_at).total_seconds() / 86400.0 if state.last_play_at else None,
            "is_buffered_offline": is_buffered,
            "event_age_s": event_age_s,
            "phone_buffer_delay_s": phone_buffer_delay
        }
        return facts_summary
