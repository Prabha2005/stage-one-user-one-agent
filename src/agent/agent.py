"""ViewerAgent: Single dedicated agent per viewer owning goal, plan, actions, and history."""
import logging
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from src.agent.fact_resolver import FactResolver
from src.agent.goal_selector import GoalSelector
from src.agent.intervention_planner import InterventionPlanner
from src.agent.verifier import SafetyVerifier
from src.config import MAX_DECISION_LATENCY_MS, UTC
from src.data.catalogue import Catalogue
from src.models.model_provider import ModelProvider, ModelProviderException
from src.store.viewer_state import ViewerState, iso, parse_ts

logger = logging.getLogger(__name__)


class ViewerAgent:
    def __init__(
        self,
        user_id: str,
        state: ViewerState,
        catalogue: Catalogue,
        goal_selector: GoalSelector,
        intervention_planner: InterventionPlanner,
        verifier: SafetyVerifier,
        model_provider: ModelProvider
    ):
        self.user_id = user_id
        self.state = state
        self.catalogue = catalogue
        self.goal_selector = goal_selector
        self.intervention_planner = intervention_planner
        self.verifier = verifier
        self.model_provider = model_provider

        self.processed_triggers: Dict[str, dict] = {} # trigger_event_id -> decision

    def process_live_event(self, event: dict):
        """Processes background live events to keep state durable and fresh."""
        self.state.apply_event(event, self.catalogue)

    def handle_trigger(
        self,
        trigger_event: dict,
        deliver_at: datetime,
        action_counter: int
    ) -> Tuple[dict, Optional[dict]]:
        """Handles a trigger event and executes the decide -> verify -> act loop.
        
        Returns:
            (decision_dict, action_dict_or_none)
        """
        start_perf = time.perf_counter()
        trigger_id = trigger_event.get("event_id", "unknown")
        user_id = trigger_event.get("user_id")

        # 1. Idempotency Check: Duplicate trigger
        if trigger_id in self.processed_triggers:
            elapsed_ms = int((time.perf_counter() - start_perf) * 1000)
            cached_dec = dict(self.processed_triggers[trigger_id])
            cached_dec["status"] = "DUPLICATE_TRIGGER_IGNORED"
            cached_dec["no_action_reason"] = "Duplicate trigger_event_id received; idempotently ignored"
            cached_dec["decided_in_ms"] = elapsed_ms
            return cached_dec, None

        # Apply trigger to viewer state
        self.state.apply_event(trigger_event, self.catalogue)

        now = deliver_at
        facts = FactResolver.resolve_facts(self.state, trigger_event, now)

        model_calls = 0
        tool_calls = 1
        fallback_used = False

        # 2. Goal Planning: Select or revise active goal
        goal_dict = None
        goal_assumptions = []
        confidence = 0.8
        confidence_reason = "Rule-based lifecycle alignment"

        # Check model provider (with fault & timeout handling)
        try:
            model_calls += 1
            prompt_context = {
                "viewer_id": user_id,
                "facts": facts,
                "candidate_goals": ["G01", "G02", "G03", "G04", "G05", "G06", "G07", "G08", "G09"]
            }
            model_res = self.model_provider.consult_model(trigger_id, prompt_context, now)

            # Injected fault: model might claim eligibility for G04 without evidence
            if model_res.get("goal_id"):
                # If model recommended a goal, we evaluate whether to accept it
                claimed_gid = model_res.get("goal_id")
                claimed_reason = model_res.get("reason", "Model assertion")
                # Temporarily consider claimed goal
                deadline = now + timedelta(hours=168)
                goal_dict = {
                    "id": claimed_gid,
                    "name": claimed_gid,
                    "started_at": iso(now),
                    "deadline": iso(deadline),
                    "evidence": [f"model_assertion:{claimed_reason}"],
                    "assumptions": ["Model assertion accepted for verification"],
                    "confidence": model_res.get("confidence", 0.9),
                    "status": "OPEN"
                }
                confidence = model_res.get("confidence", 0.9)
                confidence_reason = claimed_reason
                goal_assumptions = [f"Model claimed: {claimed_reason}"]

        except ModelProviderException as me:
            logger.warning("ModelProvider fault/timeout for %s: %s (falling back)", trigger_id, me)
            fallback_used = True
            goal_dict = None

        # If model failed or normal flow, use deterministic goal selector
        if not goal_dict:
            goal_dict, goal_assumptions, confidence, confidence_reason = self.goal_selector.select_goal(self.state, now)
            tool_calls += 1

        # 3. Intervention Planning
        action_type, action_payload, content_refs, no_action_reason = self.intervention_planner.plan_intervention(
            self.state, goal_dict, trigger_event, now, facts
        )
        tool_calls += 1

        # 4. Independent Verification
        verification = self.verifier.verify(self.state, goal_dict, action_type, action_payload, now)
        tool_calls += 1

        # If verification fails, verifier overrides action!
        if not verification.is_valid:
            logger.warning("Verifier rejected decision for %s: %s", user_id, verification.reason)
            # If goal was an unsupported model claim, discard goal
            if "eligibility_verification" in verification.failed:
                goal_dict = None
                fallback_used = True
                confidence = 0.4
                confidence_reason = f"Rejected unsupported model claim: {verification.reason}"

            action_type = "NO_ACTION"
            action_payload = None
            content_refs = []
            no_action_reason = f"Rejected by safety verifier: {verification.reason}"

        # 5. Measure Latency & Calculate Published At
        elapsed_s = time.perf_counter() - start_perf
        decided_in_ms = max(1, int(elapsed_s * 1000))
        published_at = deliver_at + timedelta(milliseconds=decided_in_ms)

        decision_id = f"D-{trigger_id[:8]}"
        action_dict = None

        if action_type != "NO_ACTION" and action_payload:
            act_id = f"A-{action_counter:05d}"
            action_dict = {
                "action_id": act_id,
                "decision_id": decision_id,
                "user_id": user_id,
                "trigger_event_id": trigger_id,
                "published_at": iso(published_at)
            }
            action_dict.update(action_payload)
            self.state.record_published_action(action_dict, published_at)

        # Build evidence list
        evidence_list = []
        if goal_dict and "evidence" in goal_dict:
            evidence_list.extend(goal_dict["evidence"])
        if content_refs:
            evidence_list.extend(content_refs[:3])
        if trigger_id:
            evidence_list.append(f"trigger_event:{trigger_id}")

        decision_output = {
            "decision_id": decision_id,
            "viewer_id": user_id,
            "trigger": {
                "event": trigger_event.get("event"),
                "received_at": trigger_event.get("received_at"),
                "event_age_s": int(facts.get("event_age_s", 0))
            },
            "goal": {
                "id": goal_dict.get("id") if goal_dict else None,
                "deadline": goal_dict.get("deadline") if goal_dict else None
            } if goal_dict else None,
            "action": action_type,
            "dialect": {
                "used": facts.get("resolved_dialect"),
                "source": facts.get("dialect_source"),
                "conflicts": facts.get("dialect_conflicts", [])
            },
            "content": content_refs,
            "confidence": round(confidence, 2),
            "confidence_reason": confidence_reason,
            "evidence": evidence_list,
            "assumptions": goal_assumptions,
            "checks": verification.to_dict(),
            "no_action_reason": no_action_reason,
            "fallback_used": fallback_used,
            "model_calls": model_calls,
            "tool_calls": tool_calls,
            "decided_in_ms": decided_in_ms,
            "status": "PUBLISHED" if action_type != "NO_ACTION" else "ABSTAINED"
        }

        # Cache decision for idempotency
        self.processed_triggers[trigger_id] = decision_output
        self.state.decisions.append(decision_output)

        return decision_output, action_dict
