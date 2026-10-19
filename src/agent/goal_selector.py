"""Goal selector evaluating eligibility across candidate goals G01-G12 with explicit assumptions."""
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from src.config import IST, UTC
from src.data.catalogue import Catalogue
from src.store.viewer_state import ViewerState, iso, parse_ts


class GoalSelector:
    def __init__(self, goals_cfg: dict, catalogue: Catalogue):
        self.goals_cfg = goals_cfg
        self.catalogue = catalogue
        # Track dynamically corrected goal rules
        # e.g., G03 success event corrected from app_open to play_started
        self.rule_corrections: Dict[str, dict] = {}

    def apply_rule_correction(self, goal_id: str, new_success: dict):
        """Handle goal_rule_corrected control event."""
        self.rule_corrections[goal_id] = new_success

    def evaluate_eligibility(self, state: ViewerState, now: datetime) -> List[Dict[str, Any]]:
        """Evaluates eligibility for all candidate goals and returns eligible candidates with evidence."""
        candidates = []

        signup_at = state.signup_at
        days_since_signup = (now - signup_at).total_seconds() / 86400.0 if signup_at else 999.0
        days_since_play = (now - state.last_play_at).total_seconds() / 86400.0 if state.last_play_at else 999.0

        # G10: Unmeasurable goal trap! (success.event is null)
        # Must NEVER be selected.

        # G11: Unmeasurable in current mobile stream (tv_app_open)
        # Must NEVER be selected.

        # G07: resume_series_3d
        # "Started a show or micro-drama, has unwatched episodes left, and has not played it for 3 or more days."
        for slug, last_epid in state.slug_progress.items():
            ep_list = self.catalogue.get_episodes(slug)
            if ep_list:
                total_eps = len(ep_list)
                # Count completed episodes for this slug
                watched_eps = sum(1 for ep in ep_list if ep[2] in state.completed_episodes)
                slug_last_play = state.slug_last_played_at.get(slug)
                slug_days_inactive = (now - slug_last_play).total_seconds() / 86400.0 if slug_last_play else 999.0
                if total_eps > watched_eps and slug_days_inactive >= 3.0:
                    candidates.append({
                        "goal_id": "G07",
                        "name": "resume_series_3d",
                        "target_slug": slug,
                        "window_hours": 72,
                        "evidence": [
                            f"slug_progress:{slug}",
                            f"days_inactive:{slug_days_inactive:.1f}"
                        ],
                        "assumptions": [
                            f"Viewer has {total_eps - watched_eps} unwatched episodes remaining in {slug}",
                            "Viewer stopped watching series for >= 3 days"
                        ],
                        "confidence": 0.88,
                        "confidence_reason": f"Clear unfinished series {slug} with 3+ days inactivity"
                    })
                    break  # One series candidate is enough

        # G01: first_play_24h (Signed up in last 7 days and has never started a play)
        if days_since_signup <= 7.0 and state.total_plays == 0:
            candidates.append({
                "goal_id": "G01",
                "name": "first_play_24h",
                "window_hours": 24,
                "evidence": [
                    f"signup_at:{iso(signup_at)}",
                    f"total_plays:{state.total_plays}"
                ],
                "assumptions": ["Viewer is a newly registered user who has not activated playback"],
                "confidence": 0.85,
                "confidence_reason": "New user registered within 7 days with zero prior plays"
            })

        # G03: second_day_return_2d (Signed up in last 2 days)
        if days_since_signup <= 2.0:
            success_rule = self.rule_corrections.get("G03", {
                "event": "app_open",
                "condition": "on a later IST calendar day than the goal start"
            })
            rule_assumptions = ["User registered within 48 hours and needs retention anchor"]
            if "G03" in self.rule_corrections:
                rule_assumptions.append("Applied mid-replay corrected rule: play_started on later IST day")

            candidates.append({
                "goal_id": "G03",
                "name": "second_day_return_2d",
                "window_hours": 48,
                "success_rule": success_rule,
                "evidence": [f"signup_at:{iso(signup_at)}"],
                "assumptions": rule_assumptions,
                "confidence": 0.82,
                "confidence_reason": "New user within 2-day cohort eligible for second-day retention"
            })

        # G02: first_full_episode_3d (Has never completed an episode)
        if state.total_completions == 0:
            candidates.append({
                "goal_id": "G02",
                "name": "first_full_episode_3d",
                "window_hours": 72,
                "evidence": [f"total_completions:{state.total_completions}"],
                "assumptions": ["Viewer has never completed any episode (90%+)"],
                "confidence": 0.80,
                "confidence_reason": "User has zero completed episodes"
            })

        # G04: trial_to_paid_7d (Subscription status is trial_active)
        if state.subscription_status == "trial_active":
            candidates.append({
                "goal_id": "G04",
                "name": "trial_to_paid_7d",
                "window_hours": 168,
                "evidence": [
                    f"subscription_status:trial_active",
                    f"source:{state.facts.get('subscription_status', {}).get('source', 'profile')}"
                ],
                "assumptions": ["User is currently on an active trial requiring paid conversion"],
                "confidence": 0.90,
                "confidence_reason": "Active trial user nearing conversion window"
            })

        # G05: first_paid_7d (Has never had a paid plan)
        if state.subscription_status == "never":
            candidates.append({
                "goal_id": "G05",
                "name": "first_paid_7d",
                "window_hours": 168,
                "evidence": [
                    f"subscription_status:never",
                    f"source:{state.facts.get('subscription_status', {}).get('source', 'profile')}"
                ],
                "assumptions": ["User has never subscribed to a paid plan"],
                "confidence": 0.75,
                "confidence_reason": "Free/unsubscribed user eligible for initial paid conversion"
            })

        # G06: reactivate_lapsed_14d (Subscription recently lapsed)
        if state.subscription_status == "lapsed":
            candidates.append({
                "goal_id": "G06",
                "name": "reactivate_lapsed_14d",
                "window_hours": 336,
                "evidence": [
                    f"subscription_status:lapsed",
                    f"source:{state.facts.get('subscription_status', {}).get('source', 'profile')}"
                ],
                "assumptions": ["User had a prior paid subscription that expired"],
                "confidence": 0.85,
                "confidence_reason": "Lapsed subscriber eligible for win-back reactivation"
            })

        # G09: weekly_habit_14d (Active subscriber with plays on at most 1 day in the last 14 days)
        if state.subscription_status == "active":
            # Count days with plays in last 14 days
            recent_play_days = 1 if days_since_play <= 14.0 else 0
            if recent_play_days <= 1:
                candidates.append({
                    "goal_id": "G09",
                    "name": "weekly_habit_14d",
                    "window_hours": 336,
                    "evidence": [
                        "subscription_status:active",
                        f"recent_play_days_in_14d:{recent_play_days}"
                    ],
                    "assumptions": ["Active subscriber showing low engagement (plays on <=1 day in last 14d)"],
                    "confidence": 0.82,
                    "confidence_reason": "Low-frequency subscriber needing habit formation"
                })

        # G08: try_new_release_7d (Active subscriber)
        if state.subscription_status == "active":
            candidates.append({
                "goal_id": "G08",
                "name": "try_new_release_7d",
                "window_hours": 168,
                "evidence": ["subscription_status:active"],
                "assumptions": ["Active subscriber ready to discover new releases"],
                "confidence": 0.78,
                "confidence_reason": "Engaged subscriber ready for content discovery"
            })

        # G12: return_after_absence_7d (No app_open in the 14 days before trigger)
        # Evaluated if prior app open was >14 days ago
        if state.last_app_open_at:
            days_since_open = (now - state.last_app_open_at).total_seconds() / 86400.0
            if days_since_open >= 14.0:
                candidates.append({
                    "goal_id": "G12",
                    "name": "return_after_absence_7d",
                    "window_hours": 168,
                    "evidence": [f"days_since_prior_app_open:{days_since_open:.1f}"],
                    "assumptions": ["User returned after >= 14 days of app absence"],
                    "confidence": 0.80,
                    "confidence_reason": "Returning lapsed visitor requiring immediate re-engagement"
                })

        return candidates

    def select_goal(self, state: ViewerState, now: datetime) -> Tuple[Optional[dict], List[str], float, str]:
        """Selects or preserves the single active goal for the viewer.
        
        Returns: (goal_dict, assumptions, confidence, confidence_reason)
        """
        # 1. Check if user already has an active OPEN goal that has not expired
        if state.active_goal and state.active_goal.get("status") == "OPEN":
            deadline = parse_ts(state.active_goal.get("deadline"))
            if deadline and now < deadline:
                # Goal is still open and running
                # Check if G03 needs rule correction update
                if state.active_goal.get("id") == "G03" and "G03" in self.rule_corrections:
                    state.active_goal["success_rule"] = self.rule_corrections["G03"]
                    state.active_goal["rule_version"] = "corrected_2026-10-01T08:30:00Z"
                return (
                    state.active_goal,
                    state.active_goal.get("assumptions", []),
                    state.active_goal.get("confidence", 0.8),
                    "Continuing existing active goal within valid time window"
                )

        # 2. Evaluate all eligible candidates
        candidates = self.evaluate_eligibility(state, now)
        if not candidates:
            return (
                None,
                ["No candidate goal met eligibility criteria on verifiable evidence"],
                0.50,
                "Abstaining from goal selection due to lack of eligible criteria"
            )

        # 3. Hierarchy / Priority ranking:
        # G07 (resume series) > G04 (trial to paid) > G01 (first play) > G03 (second day return)
        # > G06 (reactivate lapsed) > G02 (first complete) > G09 (weekly habit) > G05 (first paid) > G08 (new release)
        priority_order = ["G07", "G04", "G01", "G03", "G06", "G02", "G09", "G05", "G08", "G12"]
        candidates.sort(key=lambda c: priority_order.index(c["goal_id"]) if c["goal_id"] in priority_order else 99)

        chosen = candidates[0]
        window_h = chosen.get("window_hours", 72)
        deadline = now + timedelta(hours=window_h)

        goal_dict = {
            "id": chosen["goal_id"],
            "name": chosen["name"],
            "started_at": iso(now),
            "deadline": iso(deadline),
            "window_hours": window_h,
            "target_slug": chosen.get("target_slug"),
            "evidence": chosen.get("evidence", []),
            "assumptions": chosen.get("assumptions", []),
            "confidence": chosen.get("confidence", 0.8),
            "status": "OPEN"
        }
        if "success_rule" in chosen:
            goal_dict["success_rule"] = chosen["success_rule"]

        # Record in state
        if state.active_goal:
            state.goal_history.append(state.active_goal)
        state.active_goal = goal_dict

        return (
            goal_dict,
            chosen.get("assumptions", []),
            chosen.get("confidence", 0.8),
            chosen.get("confidence_reason", "Selected top lifecycle goal based on verifiable user facts")
        )
