"""Independent safety verifier that can reject decisions before publication."""
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple

from src.config import (
    APPROVED_WA_TEMPLATES,
    HOME_PAGE_MAX_ROWS,
    HOME_PAGE_MAX_TITLES_PER_ROW,
    HOME_PAGE_MIN_TITLES_PER_ROW,
    MAX_MESSAGES_PER_IST_DAY,
    PUSH_BODY_MAX_CHARS,
    PUSH_FORBIDDEN_PHRASES,
    PUSH_TITLE_MAX_CHARS,
    QUIET_HOURS_END_IST,
    QUIET_HOURS_START_IST,
    VALID_PRICES_INR,
    IST
)
from src.data.catalogue import Catalogue, Title
from src.store.viewer_state import ViewerState

logger = logging.getLogger(__name__)


def is_quiet_hours(dt: datetime) -> bool:
    h = dt.astimezone(IST).hour
    return h >= QUIET_HOURS_START_IST or h < QUIET_HOURS_END_IST


class VerificationResult:
    def __init__(self, passed: List[str], failed: List[str], reason: Optional[str] = None):
        self.passed = passed
        self.failed = failed
        self.reason = reason

    @property
    def is_valid(self) -> bool:
        return len(self.failed) == 0

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "failed": self.failed
        }


class SafetyVerifier:
    def __init__(self, catalogue: Catalogue):
        self.catalogue = catalogue

    def verify(
        self,
        state: ViewerState,
        goal: Optional[dict],
        action_type: str,
        action_payload: Optional[dict],
        now: datetime
    ) -> VerificationResult:
        """Runs all independent verification checks on proposed goal and action."""
        passed = []
        failed = []

        # 1. Check Holdout Arm
        if state.arm == "holdout":
            if action_type != "NO_ACTION" or action_payload is not None:
                failed.append("holdout_safety")
                return VerificationResult(
                    passed, failed,
                    f"User {state.user_id} is in holdout arm and must receive no action"
                )
            passed.append("holdout_safety")
            return VerificationResult(passed, failed)

        # 2. Check Goal Eligibility (Independent fact verification)
        if goal:
            gid = goal.get("id")
            if gid == "G10":
                failed.append("goal_measurability")
                return VerificationResult(passed, failed, "Goal G10 has null success event and is unmeasurable")
            if gid == "G11":
                failed.append("goal_measurability")
                return VerificationResult(passed, failed, "Goal G11 requires tv_app_open which is unobserved")

            # Check subscription-dependent goals against state facts (detect unsupported claims!)
            if gid == "G04" and state.subscription_status != "trial_active":
                failed.append("eligibility_verification")
                return VerificationResult(
                    passed, failed,
                    f"Unsupported claim: G04 requires trial_active, but state is {state.subscription_status}"
                )
            if gid == "G05" and state.subscription_status != "never":
                failed.append("eligibility_verification")
                return VerificationResult(
                    passed, failed,
                    f"Unsupported claim: G05 requires status never, but state is {state.subscription_status}"
                )
            if gid == "G06" and state.subscription_status != "lapsed":
                failed.append("eligibility_verification")
                return VerificationResult(
                    passed, failed,
                    f"Unsupported claim: G06 requires status lapsed, but state is {state.subscription_status}"
                )
            if gid in ("G08", "G09") and state.subscription_status != "active":
                failed.append("eligibility_verification")
                return VerificationResult(
                    passed, failed,
                    f"Unsupported claim: {gid} requires active subscriber, but state is {state.subscription_status}"
                )
            if gid == "G01" and state.total_plays > 0:
                failed.append("eligibility_verification")
                return VerificationResult(
                    passed, failed,
                    f"Unsupported claim: G01 requires zero plays, but state has {state.total_plays} plays"
                )
            passed.append("eligibility")
        else:
            passed.append("eligibility")

        # 3. Check NO_ACTION
        if action_type == "NO_ACTION":
            passed.append("abstention_validity")
            return VerificationResult(passed, failed)

        # 4. Check Home Page Action
        if action_type == "HOME_PAGE" and action_payload:
            page_d = action_payload.get("dialect")
            resolved_d = state.get_resolved_dialect()
            if page_d != resolved_d:
                failed.append("dialect_match")
                return VerificationResult(
                    passed, failed,
                    f"Page dialect {page_d} does not match viewer resolved dialect {resolved_d}"
                )
            passed.append("dialect")

            # Check hero title
            hero_id = action_payload.get("hero_title_id")
            seen_slugs = set()
            if hero_id:
                hero = self.catalogue.get_title(hero_id)
                if not hero or hero.dialect != page_d or not hero.is_available(now):
                    failed.append("hero_availability")
                    return VerificationResult(passed, failed, f"Hero title {hero_id} is unavailable or dialect mismatch")
                seen_slugs.add(hero.content_slug)

            # Check rows
            rows = action_payload.get("rows") or []
            if len(rows) > HOME_PAGE_MAX_ROWS:
                failed.append("row_limit")
                return VerificationResult(passed, failed, f"Home page has {len(rows)} rows (max {HOME_PAGE_MAX_ROWS})")

            for r_idx, r in enumerate(rows):
                t_ids = r.get("title_ids") or []
                if len(t_ids) < HOME_PAGE_MIN_TITLES_PER_ROW or len(t_ids) > HOME_PAGE_MAX_TITLES_PER_ROW:
                    failed.append("row_title_count")
                    return VerificationResult(
                        passed, failed,
                        f"Row {r_idx} has {len(t_ids)} titles (must be {HOME_PAGE_MIN_TITLES_PER_ROW}-{HOME_PAGE_MAX_TITLES_PER_ROW})"
                    )
                for tid in t_ids:
                    t = self.catalogue.get_title(tid)
                    if not t or t.dialect != page_d or not t.is_available(now):
                        failed.append("title_availability")
                        return VerificationResult(passed, failed, f"Title {tid} unavailable or wrong dialect at {now}")
                    if t.content_slug in seen_slugs:
                        failed.append("content_slug_uniqueness")
                        return VerificationResult(passed, failed, f"Duplicate content_slug {t.content_slug} on home page")
                    seen_slugs.add(t.content_slug)

            passed.append("page_constraints")
            passed.append("title_availability")

        # 5. Check Push / WhatsApp Action
        elif action_type in ("PUSH", "WHATSAPP") and action_payload:
            ch = action_type.lower()
            # Consent
            if not state.has_consent(ch):
                failed.append("consent")
                return VerificationResult(passed, failed, f"Viewer has not opted in to {ch}")
            passed.append("consent")

            # Quiet Hours
            send_at_dt = now
            if is_quiet_hours(send_at_dt):
                failed.append("quiet_hours")
                return VerificationResult(passed, failed, f"Cannot send {ch} during quiet hours (22:00-08:00 IST)")
            passed.append("quiet_hours")

            # Frequency Cap
            can_send, cap_reason = state.can_send_message(ch, send_at_dt)
            if not can_send:
                failed.append("frequency_cap")
                return VerificationResult(passed, failed, f"Frequency cap exceeded: {cap_reason}")
            passed.append("frequency_cap")

            # Content checks
            tid = action_payload.get("title_id")
            if tid:
                t = self.catalogue.get_title(tid)
                if not t or not t.is_available(now) or t.dialect != state.get_resolved_dialect():
                    failed.append("title_availability")
                    return VerificationResult(passed, failed, f"Message title {tid} unavailable or dialect mismatch")
                passed.append("title_availability")

            if ch == "push":
                text = action_payload.get("text") or {}
                t_title = text.get("title", "")
                t_body = text.get("body", "")
                if len(t_title) > PUSH_TITLE_MAX_CHARS or len(t_body) > PUSH_BODY_MAX_CHARS:
                    failed.append("copy_length")
                    return VerificationResult(passed, failed, "Push copy exceeds character limits")
                combined_text = (t_title + " " + t_body).lower()
                for phrase in PUSH_FORBIDDEN_PHRASES:
                    if phrase in combined_text:
                        failed.append("forbidden_phrases")
                        return VerificationResult(passed, failed, f"Forbidden phrase '{phrase}' in push copy")
                passed.append("copy_policy")

            elif ch == "whatsapp":
                template_id = action_payload.get("template_id")
                if template_id not in APPROVED_WA_TEMPLATES:
                    failed.append("whatsapp_template")
                    return VerificationResult(passed, failed, f"Template {template_id} is not an approved template")
                passed.append("whatsapp_template")

        return VerificationResult(passed, failed)
