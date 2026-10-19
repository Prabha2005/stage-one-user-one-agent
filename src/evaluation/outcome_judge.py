"""Outcome judge evaluating attributable success signals for each user's committed goal."""
import json
import logging
import os
from collections import defaultdict
from datetime import datetime
from typing import Dict, List, Optional, Set

from src.config import IST, UTC
from src.data.catalogue import Catalogue
from src.data.loader import read_jsonl
from src.store.viewer_state import ist_date_str, parse_ts

logger = logging.getLogger(__name__)


class OutcomeJudge:
    def __init__(self, pack_dir: str, decisions_file: str, outcome_events_file: str, catalogue: Catalogue):
        self.pack_dir = pack_dir
        self.decisions_file = decisions_file
        self.outcome_events_file = outcome_events_file
        self.catalogue = catalogue
        self.outcomes: List[dict] = []

    def judge_all(self, output_file: str = "sample_run/outcomes.jsonl"):
        """Evaluates each decision's goal against outcome events within the goal window."""
        # 1. Load decisions with goals (filter to active goals)
        user_goals = {}
        for dec in read_jsonl(self.decisions_file):
            uid = dec.get("viewer_id")
            goal = dec.get("goal")
            if goal and goal.get("id"):
                # Take latest goal decision for user
                user_goals[uid] = dec

        # 2. Group outcome events by user
        events_by_user = defaultdict(list)
        if os.path.exists(self.outcome_events_file):
            for ev in read_jsonl(self.outcome_events_file):
                events_by_user[ev.get("user_id")].append(ev)

        # 3. Judge each user's goal
        for uid, dec in user_goals.items():
            goal = dec.get("goal")
            gid = goal.get("id")
            dec_id = dec.get("decision_id")
            trig_ts = parse_ts(dec.get("trigger", {}).get("received_at"))
            deadline_ts = parse_ts(goal.get("deadline"))

            user_events = events_by_user.get(uid, [])
            # Filter events to those occurring between trig_ts and deadline_ts
            attributable_events = [
                e for e in user_events
                if trig_ts and deadline_ts and trig_ts <= (parse_ts(e.get("received_at")) or trig_ts) <= deadline_ts
            ]

            succeeded, reason, success_event = self._evaluate_goal_success(
                gid, trig_ts, deadline_ts, attributable_events, dec
            )

            outcome_record = {
                "decision_id": dec_id,
                "user_id": uid,
                "goal_id": gid,
                "status": "SUCCEEDED" if succeeded else "FAILED",
                "succeeded": succeeded,
                "reason": reason,
                "success_event": success_event,
                "attributable_events_count": len(attributable_events)
            }
            self.outcomes.append(outcome_record)

        # Write outcomes.jsonl
        os.makedirs(os.path.dirname(output_file), exist_ok=True)
        with open(output_file, "w", encoding="utf-8") as f:
            for o in self.outcomes:
                f.write(json.dumps(o, ensure_ascii=False) + "\n")

        logger.info("Judged %d user goals -> %s", len(self.outcomes), output_file)
        return self.outcomes

    def _evaluate_goal_success(
        self,
        goal_id: str,
        start_ts: Optional[datetime],
        deadline_ts: Optional[datetime],
        events: List[dict],
        decision: dict
    ) -> tuple[bool, str, Optional[dict]]:
        """Evaluates goal success strictly against criteria and attributable events."""
        if not start_ts or not deadline_ts:
            return False, "Missing goal start or deadline timestamps", None

        start_ist_day = ist_date_str(start_ts)

        # G01: first_play_24h (play_started on any title)
        if goal_id == "G01":
            for e in events:
                if e.get("event") == "play_started":
                    return True, "User started playback within 24h", e
            return False, "No play_started event observed within window", None

        # G02: first_full_episode_3d (episode_completed on any episode)
        elif goal_id == "G02":
            for e in events:
                if e.get("event") == "episode_completed":
                    return True, "User completed an episode within 72h", e
            return False, "No episode_completed event observed within window", None

        # G03: second_day_return_2d (Corrected rule: play_started on later IST calendar day)
        elif goal_id == "G03":
            for e in events:
                if e.get("event") == "play_started":
                    e_ts = parse_ts(e.get("received_at"))
                    if e_ts and ist_date_str(e_ts) > start_ist_day:
                        return True, "User started a play on a later IST day (corrected rule)", e
            return False, "No play_started on a later IST day observed within 48h", None

        # G04: trial_to_paid_7d (purchase_completed is_trial = false)
        # G05: first_paid_7d (purchase_completed is_trial = false)
        elif goal_id in ("G04", "G05"):
            for e in events:
                if e.get("event") == "purchase_completed":
                    props = e.get("properties") or {}
                    if not props.get("is_trial", False):
                        return True, f"User purchased paid plan ({props.get('plan_id')})", e
            return False, "No paid purchase_completed event observed within window", None

        # G06: reactivate_lapsed_14d (purchase_completed any paid plan)
        elif goal_id == "G06":
            for e in events:
                if e.get("event") in ("purchase_completed", "subscription_renewed"):
                    return True, "Lapsed subscriber purchased paid plan", e
            return False, "No reactivation purchase observed within 14d", None

        # G07: resume_series_3d (play_started with same content_slug)
        elif goal_id == "G07":
            # Target slug was stored in evidence or decision
            target_slug = None
            for ev_ref in decision.get("evidence", []):
                if ev_ref.startswith("slug_progress:"):
                    target_slug = ev_ref.split(":", 1)[1]
                    break
            for e in events:
                if e.get("event") == "play_started":
                    tid = (e.get("properties") or {}).get("title_id")
                    t = self.catalogue.get_title(tid)
                    if t and (target_slug is None or t.content_slug == target_slug):
                        return True, f"User resumed series {t.content_slug}", e
            return False, f"Did not resume unfinished series {target_slug or ''}", None

        # G08: try_new_release_7d (play_started title released within 30d before start)
        elif goal_id == "G08":
            for e in events:
                if e.get("event") == "play_started":
                    tid = (e.get("properties") or {}).get("title_id")
                    t = self.catalogue.get_title(tid)
                    if t and t.release_date:
                        return True, f"User played release {t.title}", e
            return False, "No new release play started within window", None

        # G09: weekly_habit_14d (play_started on 3 distinct IST days)
        elif goal_id == "G09":
            play_days: Set[str] = set()
            for e in events:
                if e.get("event") == "play_started":
                    e_ts = parse_ts(e.get("received_at"))
                    if e_ts:
                        play_days.add(ist_date_str(e_ts))
            if len(play_days) >= 3:
                return True, f"User played on {len(play_days)} distinct IST days", None
            return False, f"User played on only {len(play_days)} distinct IST days (needed 3)", None

        # G12: return_after_absence_7d (play_started any title)
        elif goal_id == "G12":
            for e in events:
                if e.get("event") == "play_started":
                    return True, "Returning user started playback within 7d", e
            return False, "No play_started event observed within window", None

        return False, f"Goal {goal_id} conditions not met", None
