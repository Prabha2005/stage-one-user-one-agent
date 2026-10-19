"""Latency reporter calculating percentiles and SLA compliance."""
import json
import logging
import math
import os
from typing import List

from src.config import FAILURE_LATENCY_MS, MAX_DECISION_LATENCY_MS

logger = logging.getLogger(__name__)


class LatencyReporter:
    @staticmethod
    def generate_report(latencies_ms: List[int], output_file: str = "sample_run/latency_report.json") -> dict:
        if not latencies_ms:
            latencies_ms = [0]

        sorted_l = sorted(latencies_ms)
        n = len(sorted_l)

        def percentile(p: float) -> int:
            idx = int(math.ceil(p * n)) - 1
            idx = max(0, min(idx, n - 1))
            return sorted_l[idx]

        count_under_60s = sum(1 for x in sorted_l if x <= MAX_DECISION_LATENCY_MS)
        count_under_5m = sum(1 for x in sorted_l if x <= FAILURE_LATENCY_MS)

        report = {
            "total_decisions": n,
            "min_ms": sorted_l[0],
            "p50_ms": percentile(0.50),
            "p90_ms": percentile(0.90),
            "p95_ms": percentile(0.95),
            "p99_ms": percentile(0.99),
            "max_ms": sorted_l[-1],
            "mean_ms": round(sum(sorted_l) / n, 2),
            "sla_compliance": {
                "under_60s_count": count_under_60s,
                "under_60s_pct": round(count_under_60s / n * 100, 2),
                "under_5m_count": count_under_5m,
                "under_5m_pct": round(count_under_5m / n * 100, 2),
                "sla_passed": count_under_60s == n
            }
        }

        os.makedirs(os.path.dirname(output_file), exist_ok=True)
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)

        logger.info("Saved latency report to %s", output_file)
        return report
