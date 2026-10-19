"""Durable per-viewer state store with provenance, conflict resolution, and history tracking."""
import json
import os
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

from src.config import IST, MAX_MESSAGES_PER_IST_DAY, UTC


def parse_ts(s: Optional[str]) -> Optional[datetime]:
    if not s or s.strip() == "":
        return None
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def iso(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def ist_date_str(dt: datetime) -> str:
    return dt.astimezone(IST).strftime("%Y-%m-%d")


class ViewerState:
    def __init__(self, user_id: str, arm: str = "agent"):
        self.user_id = user_id
        self.arm = arm  # 'agent' or 'holdout'

        # Core facts with provenance: { fact_name: {"value": ..., "source": ..., "timestamp": ..., "freshness_s": ..., "confidence": ...} }
        self.facts: Dict[str, Dict[str, Any]] = {}
        self.conflicts: List[Dict[str, Any]] = []

        # Current attributes
        self.signup_at: Optional[datetime] = None
        self.subscription_status: str = "never"  # never, trial_active, active, lapsed
        self.plan_started_at: Optional[datetime] = None
        self.plan_ends_at: Optional[datetime] = None
        self.app_dialect: Optional[str] = None
        self.profile_dialect: Optional[str] = None
        self.campaign_dialect: Optional[str] = None
        self.app_language: Optional[str] = None

        # Consent and communication
        self.notifications_opt_in: bool = False
        self.whatsapp_opt_in: bool = False
        self.opted_out_channels: Set[str] = set()

        # Behavior & History
        self.seen_event_ids: Set[str] = set()
        self.seen_session_ids: Set[str] = set()
        self.app_open_ist_days: Set[str] = set()
        self.last_app_open_at: Optional[datetime] = None
        self.last_play_at: Optional[datetime] = None
        self.total_plays: int = 0
        self.total_completions: int = 0
        self.completed_episodes: Set[str] = set()  # set of episode_id
        self.slug_progress: Dict[str, str] = {}    # slug -> last completed/watched episode_id
        self.slug_last_played_at: Dict[str, datetime] = {}
        self.genre_counts: Counter = Counter()

        # Messaging tracking: list of {"channel": ..., "action_id": ..., "sent_at": ..., "ist_day": ...}
        self.messages_sent: List[Dict[str, Any]] = []

        # Active Goal & Decisions
        self.active_goal: Optional[Dict[str, Any]] = None
        self.goal_history: List[Dict[str, Any]] = []
        self.decisions: List[Dict[str, Any]] = []
        self.actions: List[Dict[str, Any]] = []
        self.learnings: Dict[str, Any] = {
            "preferred_genres": [],
            "engagement_tier": "unknown",
            "notes": []
        }

    def _set_fact(self, name: str, value: Any, source: str, ts: datetime, confidence: float = 1.0):
        self.facts[name] = {
            "value": value,
            "source": source,
            "timestamp": iso(ts),
            "confidence": confidence
        }

    def apply_profile_snapshot(self, row: dict):
        """Apply warehouse profile snapshot with timestamp check."""
        snap_updated = parse_ts(row.get("updated_at")) or parse_ts(row.get("snapshot_at"))
        
        # Check if we already have a fresher profile snapshot
        existing_profile_fact = self.facts.get("profile_snapshot")
        if existing_profile_fact and snap_updated:
            existing_ts = parse_ts(existing_profile_fact["timestamp"])
            if existing_ts and snap_updated <= existing_ts:
                return  # Skip stale snapshot row for duplicate user

        source = f"profile:{row.get('updated_at', 'snapshot')}"
        
        # Signup
        if row.get("signup_at"):
            self.signup_at = parse_ts(row["signup_at"])
            self._set_fact("signup_at", iso(self.signup_at), source, self.signup_at or datetime.now(UTC))

        # Subscription status: only apply if no newer live event already set it
        sub_status = row.get("subscription_status", "never")
        if "subscription_status" not in self.facts or "event:" not in self.facts["subscription_status"]["source"]:
            self.subscription_status = sub_status
            self._set_fact("subscription_status", sub_status, source, snap_updated or datetime.now(UTC))

        if row.get("plan_started_at"):
            self.plan_started_at = parse_ts(row["plan_started_at"])
        if row.get("plan_ends_at"):
            self.plan_ends_at = parse_ts(row["plan_ends_at"])

        # Dialect
        prof_dialect = row.get("preferred_dialect")
        if prof_dialect:
            self.profile_dialect = prof_dialect
            self._set_fact("preferred_dialect", prof_dialect, source, snap_updated or datetime.now(UTC))

        # Consent
        self.notifications_opt_in = row.get("notifications_opt_in", "false").lower() == "true"
        self.whatsapp_opt_in = row.get("whatsapp_opt_in", "false").lower() == "true"
        self._set_fact("notifications_opt_in", self.notifications_opt_in, source, snap_updated or datetime.now(UTC))
        self._set_fact("whatsapp_opt_in", self.whatsapp_opt_in, source, snap_updated or datetime.now(UTC))

        self.facts["profile_snapshot"] = {
            "value": "loaded",
            "source": source,
            "timestamp": iso(snap_updated),
            "confidence": 1.0
        }

    def apply_event(self, event: dict, catalogue=None) -> bool:
        """Apply an event from data/events.jsonl or replay/live_events.jsonl.
        
        Returns False if duplicate event_id was rejected.
        """
        eid = event.get("event_id")
        if eid in self.seen_event_ids:
            return False  # Deduplicated!
        self.seen_event_ids.add(eid)

        ev_name = event.get("event")
        received_at = parse_ts(event.get("received_at")) or datetime.now(UTC)
        occurred_at = parse_ts(event.get("occurred_at")) or received_at
        sent_at = parse_ts(event.get("sent_at")) or received_at
        props = event.get("properties") or {}
        event_source = f"event:{eid}"

        # Track app dialect & detect conflicts
        app_d = event.get("app_dialect")
        if app_d:
            if self.app_dialect and self.app_dialect != app_d:
                self.conflicts.append({
                    "fact": "dialect",
                    "sources": {"previous_app_dialect": self.app_dialect, "current_app_dialect": app_d},
                    "resolution": f"Switched to {app_d} from {event_source} (latest user app setting)",
                    "timestamp": iso(received_at)
                })
            elif self.profile_dialect and self.profile_dialect != app_d:
                conflict_entry = {
                    "fact": "dialect",
                    "sources": {"profile": self.profile_dialect, "app_setting": app_d},
                    "resolution": f"Used {app_d} because the client UI renders in app_dialect",
                    "timestamp": iso(received_at)
                }
                if conflict_entry not in self.conflicts:
                    self.conflicts.append(conflict_entry)
            self.app_dialect = app_d
            self._set_fact("app_dialect", app_d, event_source, received_at)

        # Handle deeplink dialect conflict if present
        deeplink = props.get("deeplink")
        if deeplink and "ott://c/" in deeplink:
            # format: ott://c/<campaign>/<dialect>/title/<title_id>
            # parts: ['ott:', '', 'c', '<campaign>', '<dialect>', 'title', '<title_id>']
            parts = deeplink.split("/")
            if len(parts) >= 5:
                camp_d = parts[4]
                self.campaign_dialect = camp_d
                if app_d and camp_d != app_d:
                    self.conflicts.append({
                        "fact": "dialect",
                        "sources": {"campaign_deeplink": camp_d, "app_setting": app_d},
                        "resolution": f"Used {app_d} (app setting takes precedence over campaign link for rendered content)",
                        "timestamp": iso(received_at)
                    })

        # Process specific events
        if ev_name == "signup_completed":
            self.signup_at = received_at
            self._set_fact("signup_at", iso(received_at), event_source, received_at)
            self.subscription_status = "never"
            self._set_fact("subscription_status", "never", event_source, received_at)

        elif ev_name == "app_open":
            sid = event.get("session_id")
            if sid:
                self.seen_session_ids.add(sid)
            self.last_app_open_at = received_at
            self.app_open_ist_days.add(ist_date_str(received_at))
            self._set_fact("last_app_open_at", iso(received_at), event_source, received_at)

        elif ev_name == "play_started":
            self.total_plays += 1
            self.last_play_at = received_at
            tid = props.get("title_id")
            epid = props.get("episode_id")
            if catalogue and tid:
                t = catalogue.get_title(tid)
                if t:
                    for g in t.genres:
                        self.genre_counts[g] += 1
                    self.slug_last_played_at[t.content_slug] = received_at
                    if epid:
                        self.slug_progress[t.content_slug] = epid
            self._set_fact("last_play_at", iso(received_at), event_source, received_at)

        elif ev_name == "episode_completed":
            self.total_completions += 1
            epid = props.get("episode_id")
            tid = props.get("title_id")
            if epid:
                self.completed_episodes.add(epid)
            if catalogue and tid:
                t = catalogue.get_title(tid)
                if t and epid:
                    self.slug_progress[t.content_slug] = epid

        elif ev_name == "purchase_completed":
            is_trial = props.get("is_trial", False)
            if is_trial:
                self.subscription_status = "trial_active"
            else:
                self.subscription_status = "active"
            self._set_fact("subscription_status", self.subscription_status, event_source, received_at)
            self.plan_started_at = received_at

            # If user had a first_paid_7d or trial_to_paid_7d goal, mark it as resolved or superseded
            if self.active_goal and self.active_goal.get("status") == "OPEN":
                gid = self.active_goal.get("id")
                if gid in ("G04", "G05") and not is_trial:
                    self.active_goal["status"] = "SUCCEEDED"
                    self.active_goal["succeeded_at"] = iso(received_at)
                    self.active_goal["success_reason"] = f"Purchased paid plan at {iso(received_at)}"

        elif ev_name == "subscription_renewed":
            self.subscription_status = "active"
            self._set_fact("subscription_status", "active", event_source, received_at)

        elif ev_name == "subscription_lapsed":
            self.subscription_status = "lapsed"
            self._set_fact("subscription_status", "lapsed", event_source, received_at)

        elif ev_name == "notification_opt_out":
            channel = props.get("channel", "push")
            self.opted_out_channels.add(channel)
            if channel == "push":
                self.notifications_opt_in = False
                self._set_fact("notifications_opt_in", False, event_source, received_at)
            elif channel == "whatsapp":
                self.whatsapp_opt_in = False
                self._set_fact("whatsapp_opt_in", False, event_source, received_at)

        return True

    def get_resolved_dialect(self) -> str:
        """Resolve dialect with strict precedence: app_dialect > campaign_dialect > profile_dialect."""
        if self.app_dialect:
            return self.app_dialect
        if self.campaign_dialect:
            return self.campaign_dialect
        if self.profile_dialect:
            return self.profile_dialect
        return "d1"  # Default fallback if unknown

    def has_consent(self, channel: str) -> bool:
        if channel in self.opted_out_channels:
            return False
        if channel == "push":
            return self.notifications_opt_in
        if channel == "whatsapp":
            return self.whatsapp_opt_in
        if channel == "in_app_message":
            return True  # In-app message does not require push/wa consent
        return False

    def can_send_message(self, channel: str, at: datetime) -> Tuple[bool, str]:
        """Check consent and frequency cap for push or whatsapp."""
        if channel in ("push", "whatsapp"):
            if not self.has_consent(channel):
                return False, f"no_{channel}_consent"
            day = ist_date_str(at)
            # Count push + whatsapp on this IST day
            sent_today = sum(
                1 for m in self.messages_sent
                if m.get("ist_day") == day and m.get("channel") in ("push", "whatsapp")
            )
            if sent_today >= MAX_MESSAGES_PER_IST_DAY:
                return False, "frequency_cap_reached"
        elif channel == "in_app_message":
            day = ist_date_str(at)
            sent_today = sum(
                1 for m in self.messages_sent
                if m.get("ist_day") == day and m.get("channel") == "in_app_message"
            )
            if sent_today >= 1:
                return False, "in_app_daily_cap_reached"
        return True, "ok"

    def record_published_action(self, action: dict, at: datetime):
        """Record published action and update message history."""
        self.actions.append(action)
        act_type = action.get("type")
        if act_type in ("push", "whatsapp", "in_app_message"):
            self.messages_sent.append({
                "channel": act_type,
                "action_id": action.get("action_id"),
                "sent_at": iso(at),
                "ist_day": ist_date_str(at)
            })

    def to_dict(self) -> dict:
        return {
            "user_id": self.user_id,
            "arm": self.arm,
            "subscription_status": self.subscription_status,
            "signup_at": iso(self.signup_at),
            "resolved_dialect": self.get_resolved_dialect(),
            "app_dialect": self.app_dialect,
            "profile_dialect": self.profile_dialect,
            "notifications_opt_in": self.notifications_opt_in,
            "whatsapp_opt_in": self.whatsapp_opt_in,
            "opted_out_channels": sorted(list(self.opted_out_channels)),
            "total_plays": self.total_plays,
            "total_completions": self.total_completions,
            "completed_episodes_count": len(self.completed_episodes),
            "active_series_in_progress": len(self.slug_progress),
            "top_genres": [g for g, _ in self.genre_counts.most_common(3)],
            "active_goal": self.active_goal,
            "goal_history_count": len(self.goal_history),
            "decisions_count": len(self.decisions),
            "actions_count": len(self.actions),
            "facts": self.facts,
            "conflicts": self.conflicts,
            "learnings": self.learnings
        }

    def save(self, filepath: str):
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)
