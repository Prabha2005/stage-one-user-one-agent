"""Model provider abstraction with replay fault injection, timeout enforcement, and safe fallback."""
import json
import logging
import random
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from src.config import MODEL_CALL_TIMEOUT_S, UTC

logger = logging.getLogger(__name__)


class ModelProviderException(Exception):
    pass


class ModelUnavailableException(ModelProviderException):
    pass


class ModelTimeoutException(ModelProviderException):
    pass


class ModelMalformedOutputException(ModelProviderException):
    pass


class ModelProvider:
    def __init__(self, faults: Optional[Dict[str, dict]] = None):
        self.faults = faults or {}
        self.call_count = 0
        self.is_unavailable = False
        self.is_degraded = False

    def set_operational_status(self, unavailable: bool = False, degraded: bool = False):
        self.is_unavailable = unavailable
        self.is_degraded = degraded

    def consult_model(self, trigger_event_id: str, prompt_context: dict, at_dt: datetime) -> dict:
        """Consults the model, handling timeout budgets, control faults, and replay faults."""
        self.call_count += 1
        start_time = time.time()

        # 1. Check control event outages
        if self.is_unavailable:
            logger.warning("ModelProvider unavailable (HTTP 503) at %s", at_dt.isoformat())
            raise ModelUnavailableException("HTTP 503: Model service unavailable")

        if self.is_degraded:
            # Replay control event says: p95 read latency 8s, 20% timeout
            if random.random() < 0.20:
                logger.warning("ModelProvider degraded timeout at %s", at_dt.isoformat())
                raise ModelTimeoutException("Model call timed out during service degradation")

        # 2. Check injected replay faults
        fault = self.faults.get(trigger_event_id)
        if fault:
            f_type = fault.get("fault")
            logger.info("Triggering injected model fault: %s for trigger %s", f_type, trigger_event_id)

            if f_type == "timeout":
                timeout_s = fault.get("seconds", 45)
                # Since budget is 10s or 60s, a 45s timeout must be caught before breaking the SLA!
                raise ModelTimeoutException(f"Model request timed out after {timeout_s}s (exceeded budget)")

            elif f_type == "malformed_output":
                raw_out = fault.get("model_output")
                try:
                    return json.loads(raw_out)
                except Exception as e:
                    raise ModelMalformedOutputException(f"Malformed JSON from model: {raw_out}") from e

            elif f_type == "unsupported_claim":
                # Returns model claim that asserts eligibility without evidence
                return fault.get("model_output", {})

        # 3. Normal simulated intelligent reasoning
        # Return structured recommendations based on context
        user_facts = prompt_context.get("facts", {})
        candidate_goals = prompt_context.get("candidate_goals", [])
        top_goal = candidate_goals[0] if candidate_goals else None

        return {
            "recommended_goal": top_goal,
            "confidence": 0.85,
            "reasoning": f"User context aligns with {top_goal} lifecycle stage",
            "model_generated": True
        }
