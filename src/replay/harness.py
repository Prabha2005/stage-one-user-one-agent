"""Replay harness for streaming triggers, live events, and control events in chronological order."""
import json
import logging
import os
from datetime import datetime, timezone
from typing import Dict, List, Optional

from src.agent.agent import ViewerAgent
from src.agent.goal_selector import GoalSelector
from src.agent.intervention_planner import InterventionPlanner
from src.agent.verifier import SafetyVerifier
from src.data.catalogue import Catalogue
from src.data.loader import DataLoader, read_jsonl
from src.models.model_provider import ModelProvider
from src.store.viewer_state import ViewerState, parse_ts

logger = logging.getLogger(__name__)
UTC = timezone.utc


class ReplayHarness:
    def __init__(self, root_dir: str, output_dir: str = "sample_run"):
        self.root_dir = root_dir
        self.output_dir = output_dir
        self.loader = DataLoader(root_dir)

        # Core data structures
        self.arms: Dict[str, str] = {}
        self.profiles: Dict[str, dict] = {}
        self.catalogue: Optional[Catalogue] = None
        self.model_provider: Optional[ModelProvider] = None
        self.goal_selector: Optional[GoalSelector] = None
        self.intervention_planner: Optional[InterventionPlanner] = None
        self.verifier: Optional[SafetyVerifier] = None

        # Per-user state and agents: user_id -> ViewerAgent
        self.viewer_states: Dict[str, ViewerState] = {}
        self.agents: Dict[str, ViewerAgent] = {}

        # Replay event queues
        self.triggers: List[dict] = []
        self.live_events: List[dict] = []
        self.control_events: List[dict] = []

        # Outputs
        self.decisions: List[dict] = []
        self.actions: List[dict] = []
        self.latencies_ms: List[int] = []

    def setup(self):
        """Initializes all data, configs, agents, and event streams."""
        logger.info("Initializing ReplayHarness...")
        os.makedirs(self.output_dir, exist_ok=True)
        os.makedirs(os.path.join(self.output_dir, "viewer_state"), exist_ok=True)

        self.arms = self.loader.load_arms()
        self.profiles = self.loader.load_profiles()
        self.catalogue = Catalogue(os.path.join(self.root_dir, "data"))

        goals_cfg, interventions_cfg, pricing_cfg = self.loader.load_configs()
        faults = self.loader.load_model_faults()

        self.model_provider = ModelProvider(faults)
        self.goal_selector = GoalSelector(goals_cfg, self.catalogue)
        self.intervention_planner = InterventionPlanner(self.catalogue, interventions_cfg)
        self.verifier = SafetyVerifier(self.catalogue)

        # 1. Initialize viewer states from profile snapshot and arms
        # Also ensure brand new users without profiles are initialized with default state
        all_user_ids = set(self.arms.keys())
        all_user_ids.update(self.profiles.keys())

        for uid in all_user_ids:
            arm = self.arms.get(uid, "agent")
            state = ViewerState(uid, arm)
            if uid in self.profiles:
                state.apply_profile_snapshot(self.profiles[uid])
            self.viewer_states[uid] = state

        # 2. Ingest historical events (data/events.jsonl)
        logger.info("Ingesting 30 days of historical events...")
        for event in self.loader.stream_historical_events():
            uid = event.get("user_id")
            if uid not in self.viewer_states:
                arm = self.arms.get(uid, "agent")
                self.viewer_states[uid] = ViewerState(uid, arm)
            self.viewer_states[uid].apply_event(event, self.catalogue)

        # 3. Create dedicated agent for each user
        for uid, state in self.viewer_states.items():
            self.agents[uid] = ViewerAgent(
                user_id=uid,
                state=state,
                catalogue=self.catalogue,
                goal_selector=self.goal_selector,
                intervention_planner=self.intervention_planner,
                verifier=self.verifier,
                model_provider=self.model_provider
            )

        # 4. Load replay streams
        self.triggers = list(read_jsonl(os.path.join(self.root_dir, "replay", "triggers.jsonl")))
        self.live_events = list(read_jsonl(os.path.join(self.root_dir, "replay", "live_events.jsonl")))
        self.control_events = self.loader.load_control_events()

        logger.info(
            "Setup complete. Users: %d, Triggers: %d, Live events: %d, Control events: %d",
            len(self.viewer_states), len(self.triggers), len(self.live_events), len(self.control_events)
        )

    def run(self):
        """Executes the replay day in strict timestamp order."""
        logger.info("Starting replay execution...")

        # Tag and combine events into an event timeline:
        # Each item: (sort_timestamp, event_type, payload)
        timeline = []

        for trig in self.triggers:
            deliver_at = parse_ts(trig.get("deliver_at") or trig.get("received_at"))
            timeline.append((deliver_at, "trigger", trig))

        for live in self.live_events:
            rec_at = parse_ts(live.get("received_at"))
            timeline.append((rec_at, "live", live))

        for ctl in self.control_events:
            ctl_at = parse_ts(ctl.get("at"))
            timeline.append((ctl_at, "control", ctl))

        # Sort strictly by timestamp
        timeline.sort(key=lambda item: item[0] if item[0] else datetime.min.replace(tzinfo=UTC))

        action_counter = 0

        # Active control windows
        ctl_unavailable_until: Optional[datetime] = None
        ctl_degraded_until: Optional[datetime] = None

        for item_ts, event_type, payload in timeline:
            if not item_ts:
                continue

            # Check status of control windows
            if ctl_unavailable_until and item_ts > ctl_unavailable_until:
                ctl_unavailable_until = None
            if ctl_degraded_until and item_ts > ctl_degraded_until:
                ctl_degraded_until = None

            self.model_provider.set_operational_status(
                unavailable=bool(ctl_unavailable_until),
                degraded=bool(ctl_degraded_until)
            )

            # 1. Handle Control Events
            if event_type == "control":
                c_type = payload.get("type")
                logger.info("Executing Control Event at %s: %s", item_ts.isoformat(), c_type)

                if c_type == "title_withdrawn":
                    tid = payload.get("title_id")
                    self.catalogue.withdraw_title(tid, item_ts)

                elif c_type == "goal_rule_corrected":
                    gid = payload.get("goal_id")
                    new_succ = payload.get("new_success")
                    logger.info("Applying dynamic goal rule correction for %s: %s", gid, new_succ)
                    self.goal_selector.apply_rule_correction(gid, new_succ)
                    # Dynamically update existing open goals in memory for all affected users!
                    for state in self.viewer_states.values():
                        if state.active_goal and state.active_goal.get("id") == gid and state.active_goal.get("status") == "OPEN":
                            state.active_goal["success_rule"] = new_succ
                            state.active_goal["rule_version"] = f"corrected_{item_ts.isoformat()}"

                elif c_type == "dependency_unavailable":
                    until_ts = parse_ts(payload.get("until"))
                    ctl_unavailable_until = until_ts
                    self.model_provider.set_operational_status(unavailable=True)
                    logger.warning("Dependency unavailable until %s", until_ts)

                elif c_type == "dependency_degraded":
                    until_ts = parse_ts(payload.get("until"))
                    ctl_degraded_until = until_ts
                    self.model_provider.set_operational_status(degraded=True)
                    logger.warning("Dependency degraded until %s", until_ts)

            # 2. Handle Live Events (purchases, organic plays, opt-outs)
            elif event_type == "live":
                uid = payload.get("user_id")
                if uid in self.agents:
                    self.agents[uid].process_live_event(payload)

            # 3. Handle Triggers (app_open, login)
            elif event_type == "trigger":
                uid = payload.get("user_id")
                if uid not in self.agents:
                    # Dynamically initialize if brand new
                    arm = self.arms.get(uid, "agent")
                    st = ViewerState(uid, arm)
                    self.viewer_states[uid] = st
                    self.agents[uid] = ViewerAgent(
                        user_id=uid,
                        state=st,
                        catalogue=self.catalogue,
                        goal_selector=self.goal_selector,
                        intervention_planner=self.intervention_planner,
                        verifier=self.verifier,
                        model_provider=self.model_provider
                    )

                agent = self.agents[uid]
                action_counter += 1
                decision_dict, action_dict = agent.handle_trigger(payload, item_ts, action_counter)

                self.decisions.append(decision_dict)
                self.latencies_ms.append(decision_dict.get("decided_in_ms", 1))

                if action_dict:
                    self.actions.append(action_dict)

        logger.info(
            "Replay complete! Generated %d decisions, %d actions.",
            len(self.decisions), len(self.actions)
        )
        self.save_outputs()

    def save_outputs(self):
        """Saves decisions.jsonl, actions.jsonl, and per-user state files."""
        # 1. Save decisions.jsonl
        dec_path = os.path.join(self.output_dir, "decisions.jsonl")
        with open(dec_path, "w", encoding="utf-8") as f:
            for d in self.decisions:
                f.write(json.dumps(d, ensure_ascii=False) + "\n")
        logger.info("Saved decisions to %s", dec_path)

        # 2. Save actions.jsonl
        act_path = os.path.join(self.output_dir, "actions.jsonl")
        with open(act_path, "w", encoding="utf-8") as f:
            for a in self.actions:
                f.write(json.dumps(a, ensure_ascii=False) + "\n")
        logger.info("Saved actions to %s", act_path)

        # 3. Save durable per-viewer states
        state_dir = os.path.join(self.output_dir, "viewer_state")
        for uid, state in self.viewer_states.items():
            # Save all users who had decisions or activity
            if state.decisions or state.actions or uid in self.triggers:
                st_file = os.path.join(state_dir, f"{uid}.json")
                state.save(st_file)
        logger.info("Saved viewer state files in %s", state_dir)
