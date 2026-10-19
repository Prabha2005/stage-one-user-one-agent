"""Intervention planner proposing proportionate actions (home page, push, whatsapp, or no-action)."""
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple

from src.config import (
    HOME_PAGE_MAX_ROWS,
    HOME_PAGE_MAX_TITLES_PER_ROW,
    HOME_PAGE_MIN_TITLES_PER_ROW,
    QUIET_HOURS_END_IST,
    QUIET_HOURS_START_IST,
    IST
)
from src.data.catalogue import Catalogue, Title
from src.store.viewer_state import ViewerState, iso

logger = logging.getLogger(__name__)


def is_quiet_hours(dt: datetime) -> bool:
    h = dt.astimezone(IST).hour
    return h >= QUIET_HOURS_START_IST or h < QUIET_HOURS_END_IST


class InterventionPlanner:
    def __init__(self, catalogue: Catalogue, interventions_cfg: dict):
        self.catalogue = catalogue
        self.interventions_cfg = interventions_cfg

    def plan_intervention(
        self,
        state: ViewerState,
        goal: Optional[dict],
        trigger_event: dict,
        now: datetime,
        facts: dict
    ) -> Tuple[str, Optional[dict], List[str], Optional[str]]:
        """Plans an intervention based on viewer state, active goal, and trigger.
        
        Returns:
            (action_type, action_payload, content_refs, no_action_reason)
        """
        user_id = state.user_id

        # 1. Rule: Holdout arm gets ZERO actions
        if state.arm == "holdout":
            return "NO_ACTION", None, [], "Viewer is in the holdout control arm"

        # 2. Rule: Stale / Offline-buffered trigger
        if facts.get("is_buffered_offline"):
            age_s = facts.get("event_age_s", 0)
            return "NO_ACTION", None, [], f"Trigger was buffered offline ({age_s:.0f}s old); not a live interactive session"

        # 3. Rule: No goal selected
        if not goal:
            return "NO_ACTION", None, [], "No active goal or eligible lifecycle stage"

        event_name = trigger_event.get("event")
        resolved_dialect = facts.get("resolved_dialect", "d1")

        # 4. App open / login trigger -> Real-time Personalized Home Page
        if event_name in ("app_open", "login_success"):
            return self._build_home_page(state, goal, now, resolved_dialect)

        # 5. Default abstention
        return "NO_ACTION", None, [], f"No proportionate intervention for trigger event '{event_name}'"

    def _build_home_page(
        self,
        state: ViewerState,
        goal: dict,
        now: datetime,
        dialect: str
    ) -> Tuple[str, Optional[dict], List[str], Optional[str]]:
        """Constructs a compliant, optimized personalized home page."""
        available_titles = self.catalogue.get_available_titles(dialect, now)
        if len(available_titles) < HOME_PAGE_MIN_TITLES_PER_ROW:
            return "NO_ACTION", None, [], f"Fewer than {HOME_PAGE_MIN_TITLES_PER_ROW} available titles in dialect {dialect}"

        seen_slugs: Set[str] = set()
        content_refs: List[str] = []
        rows: List[dict] = []

        # 1. Hero Title Selection
        # Pick a high-relevance title (e.g. unfinished series or top genre)
        hero_title: Optional[Title] = None
        target_slug = goal.get("target_slug")
        if target_slug and target_slug in self.catalogue.slug_to_titles:
            for t in self.catalogue.slug_to_titles[target_slug]:
                if t.dialect == dialect and t.is_available(now):
                    hero_title = t
                    break

        if not hero_title:
            # Fallback hero from top genre or premium title
            top_genres = [g for g, _ in state.genre_counts.most_common(2)]
            for t in available_titles:
                if top_genres and any(g in t.genres for g in top_genres):
                    hero_title = t
                    break
        if not hero_title and available_titles:
            hero_title = available_titles[0]

        if hero_title:
            seen_slugs.add(hero_title.content_slug)
            content_refs.append(f"title:{hero_title.title_id}")

        # 2. Row: Next Episode (if user has active series progress)
        next_ep_titles: List[str] = []
        for slug in state.slug_progress:
            if slug in seen_slugs:
                continue
            for t in self.catalogue.slug_to_titles.get(slug, []):
                if t.dialect == dialect and t.is_available(now):
                    next_ep_titles.append(t.title_id)
                    seen_slugs.add(slug)
                    content_refs.append(f"title:{t.title_id}")
                    break
            if len(next_ep_titles) >= HOME_PAGE_MAX_TITLES_PER_ROW:
                break

        if len(next_ep_titles) >= HOME_PAGE_MIN_TITLES_PER_ROW:
            rows.append({
                "row_type": "next_episode",
                "title_ids": next_ep_titles
            })
        else:
            # Drop incomplete next_episode row to satisfy "row with <3 titles dropped"
            for tid in next_ep_titles:
                t = self.catalogue.get_title(tid)
                if t and t.content_slug in seen_slugs:
                    seen_slugs.remove(t.content_slug)
                if f"title:{tid}" in content_refs:
                    content_refs.remove(f"title:{tid}")

        # 3. Row: Because You Watched / Genre (personalized to user's top genre)
        top_genres = [g for g, _ in state.genre_counts.most_common(2)]
        for g in top_genres:
            if len(rows) >= HOME_PAGE_MAX_ROWS:
                break
            genre_titles: List[str] = []
            for t in available_titles:
                if t.content_slug not in seen_slugs and g in t.genres:
                    genre_titles.append(t.title_id)
                    seen_slugs.add(t.content_slug)
                    content_refs.append(f"title:{t.title_id}")
                    if len(genre_titles) >= min(6, HOME_PAGE_MAX_TITLES_PER_ROW):
                        break
            if len(genre_titles) >= HOME_PAGE_MIN_TITLES_PER_ROW:
                rows.append({
                    "row_type": "because_you_watched",
                    "title_ids": genre_titles
                })
            else:
                for tid in genre_titles:
                    t = self.catalogue.get_title(tid)
                    if t and t.content_slug in seen_slugs:
                        seen_slugs.remove(t.content_slug)
                    if f"title:{tid}" in content_refs:
                        content_refs.remove(f"title:{tid}")

        # 4. Row: New Releases
        if len(rows) < HOME_PAGE_MAX_ROWS:
            new_titles: List[str] = []
            # Sort by release date descending
            sorted_avail = sorted(
                available_titles,
                key=lambda x: x.release_date or "1970-01-01",
                reverse=True
            )
            for t in sorted_avail:
                if t.content_slug not in seen_slugs:
                    new_titles.append(t.title_id)
                    seen_slugs.add(t.content_slug)
                    content_refs.append(f"title:{t.title_id}")
                    if len(new_titles) >= min(6, HOME_PAGE_MAX_TITLES_PER_ROW):
                        break
            if len(new_titles) >= HOME_PAGE_MIN_TITLES_PER_ROW:
                rows.append({
                    "row_type": "new_releases",
                    "title_ids": new_titles
                })
            else:
                for tid in new_titles:
                    t = self.catalogue.get_title(tid)
                    if t and t.content_slug in seen_slugs:
                        seen_slugs.remove(t.content_slug)
                    if f"title:{tid}" in content_refs:
                        content_refs.remove(f"title:{tid}")

        # 5. Row: Popular In Dialect
        if len(rows) < HOME_PAGE_MAX_ROWS:
            pop_titles: List[str] = []
            for t in available_titles:
                if t.content_slug not in seen_slugs:
                    pop_titles.append(t.title_id)
                    seen_slugs.add(t.content_slug)
                    content_refs.append(f"title:{t.title_id}")
                    if len(pop_titles) >= min(6, HOME_PAGE_MAX_TITLES_PER_ROW):
                        break
            if len(pop_titles) >= HOME_PAGE_MIN_TITLES_PER_ROW:
                rows.append({
                    "row_type": "popular_in_dialect",
                    "title_ids": pop_titles
                })
            else:
                for tid in pop_titles:
                    t = self.catalogue.get_title(tid)
                    if t and t.content_slug in seen_slugs:
                        seen_slugs.remove(t.content_slug)
                    if f"title:{tid}" in content_refs:
                        content_refs.remove(f"title:{tid}")

        if not rows and not hero_title:
            return "NO_ACTION", None, [], "Unable to assemble any valid rows with >=3 unique titles"

        page_payload = {
            "type": "home_page",
            "dialect": dialect,
            "hero_title_id": hero_title.title_id if hero_title else None,
            "rows": rows
        }

        return "HOME_PAGE", page_payload, content_refs, None
