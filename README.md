# STAGE — One User, One Agent

Autonomous viewer lifecycle agent engine for regional-dialect OTT streaming.  
Built for the STAGE AI Engineering Take-Home Challenge.

---

## 🚀 Quick Start (One Command Execution)

The system requires **Python 3.9+** and uses only standard library components (plus `pytest` for testing). No personal API keys or cloud services required.

### 1. Setup Environment
```bash
# Clone the repository
git clone <repo-url>
cd stage

# Install pytest (optional, for running tests)
pip install pytest
```

### 2. Run Full Replay, Simulation, & Reporting (One Command)
```bash
python -m src.cli run-all --pack . --out sample_run
```

This single command executes the complete end-to-end lifecycle:
1. **Replay Harness**: Streams 2,769 triggers in delivery order, handles live events, catches injected model faults, enforces quiet hours and consent, and writes `sample_run/decisions.jsonl` and `sample_run/actions.jsonl`.
2. **Outcome Simulator**: Simulates 14 days of post-action viewer behavior into `sample_run/sim/`.
3. **Outcome Judge**: Judges each active goal against attributable outcome events and writes `sample_run/outcomes.jsonl`.
4. **Impact & Latency Reports**: Generates `sample_run/latency_report.json`, `sample_run/impact_report.md`, and `sample_run/final_report.md`.

### 3. Run Automated Tests
```bash
python -m pytest tests/test_agent_system.py -v
```

---

## 📂 Repository Structure

```
├── README.md                      # Project overview and one-command run instructions
├── ARCHITECTURE.md                # System architecture, state ownership, trust boundaries
├── AI_COLLABORATION.md            # AI collaboration log with accepted/rejected proposals
├── KNOWN_LIMITATIONS.md           # Intentionally scoped decisions and engineering rationale
├── DATA_PACK_README.md            # Original data pack specification
│
├── config/                        # Platform policy and configurations
│   ├── goals.json                 # 12 candidate goals with eligibility and success rules
│   ├── interventions.json         # Constraints for home page, push, and WhatsApp
│   ├── pricing.json               # Canonical subscription plans and pricing
│   └── messaging_policy.md        # Consent, quiet hours, frequency cap, and privacy rules
│
├── data/                          # 30-day historical data pack
│   ├── events.jsonl               # 142,906 historical events (with duplicates and late events)
│   ├── profiles_snapshot.csv      # Warehouse export (with 1-day staleness and duplicates)
│   ├── catalogue.csv              # 2,014 titles across 6 dialects (with availability windows)
│   └── episodes.csv               # Episode metadata for series completion tracking
│
├── pilot/                         # Earlier pilot results and internal summary report
│   ├── pilot_assignment.csv       # Randomized assignment (treatment vs holdout)
│   ├── pilot_outcomes.csv         # Individual viewer pilot outcomes
│   └── pilot_summary_report.md    # Internal report demonstrating selection bias
│
├── replay/                        # Replay day trigger stream and operational surprises
│   ├── triggers.jsonl             # 2,769 timed triggers in delivery order
│   ├── live_events.jsonl          # In-day events (purchases, organic plays, opt-outs)
│   ├── control_events.jsonl       # Title withdrawals, rule corrections, and dependency outages
│   ├── model_faults.jsonl         # Injected model faults (unsupported claims, malformed JSON, timeouts)
│   └── arms.csv                   # Randomized assignment (agent vs holdout)
│
├── simulator/
│   └── simulate_outcomes.py       # Deterministic 14-day outcome simulator
│
├── src/                           # Production source code
│   ├── cli.py                     # Command-line interface with run-all command
│   ├── config.py                  # Global policy constants and SLA thresholds
│   ├── data/                      # Data loading and catalogue index with injection defense
│   ├── store/                     # Durable ViewerState store with provenance tracking
│   ├── models/                    # ModelProvider abstraction with replay fault handling
│   ├── agent/                     # ViewerAgent, FactResolver, GoalSelector, InterventionPlanner, SafetyVerifier
│   ├── replay/                    # ReplayHarness coordinating chronological execution
│   └── evaluation/                # OutcomeJudge, ImpactReporter, LatencyReporter
│
├── tests/
│   └── test_agent_system.py       # Automated unit & integration test suite
│
└── sample_run/                    # Execution artifacts from full replay run
    ├── decisions.jsonl            # Auditable decision log for every trigger
    ├── actions.jsonl              # Published actions accepted by the platform
    ├── outcomes.jsonl             # Evaluated goal outcomes (SUCCEEDED / FAILED)
    ├── latency_report.json        # Sub-50ms latency distribution and SLA compliance
    ├── impact_report.md           # ITT causal impact analysis and pilot re-evaluation
    ├── final_report.md            # Comprehensive engineering discussion report
    ├── viewer_state/              # Durable JSON state per viewer
    └── sim/                       # Simulator output events and policy verification report
```

---

## 🎯 Key Traps Handled

| Challenge | Trap Found in Data | How the System Solves It |
|---|---|---|
| **Data Imperfections** | 2,176 duplicate event IDs in `events.jsonl` | Deduplication on ingestion via `seen_event_ids` set. |
| **Warehouse Staleness** | `profiles_snapshot.csv` is 1 day stale with 30 duplicate users | Selects freshest row by `updated_at`; newer live events override snapshot facts. |
| **New Users** | 50 users in events have no profile row | Initializes state directly from raw events (`signup_completed`) without crashing. |
| **Network & Clocks** | 455 offline-buffered app opens (phone delay > 10m) | Detects delay via `(sent_at - occurred_at) > 600s`; abstains from interactive actions (`NO_ACTION`). |
| **Dialect Conflicts** | App setting, profile, and campaign links disagree | Strict precedence: App Setting > Campaign Link > Profile. Conflicts tracked in output. |
| **Adversarial Content** | Prompt injections in catalogue descriptions (`T0002`, `T0522`) | Sanitizes descriptions, strips fake instructions, and treats all catalogue text as passive content. |
| **Operational Outages** | Model provider HTTP 503 outage & read degradation | Caught instantly; logs fallback; activates deterministic rule engine within SLA (<25ms). |
| **Dynamic Changes** | Title withdrawn (`T0001` at 07:00 UTC); G03 rule corrected at 08:30 UTC | Updates catalogue availability dynamically; applies updated rule only to open G03 goals. |
| **Unmeasurable Goals** | `G10` (success event is null) & `G11` (`tv_app_open` absent) | Disqualifies both goals with documented reasons. |
| **Measurement Bias** | Pilot report claimed +22.4% uplift by comparing served vs unserved | Recomputes randomized ITT (-1.30%) and unmasks severe selection bias. |

---

## 📊 Evaluation & Impact Summary

- **Total Triggers Processed**: 2,769
- **Policy Violations**: **0** (Zero quiet hours violations, zero consent breaches, zero frequency cap violations).
- **Holdout Isolation**: **0 actions on holdout users** (Strict holdout compliance).
- **Home Page Acceptance**: **1,091 / 1,091 accepted** (100% compliant with row limits, unique slugs, and availability).
- **Decision Latency**: Mean < 25 ms, p99 < 50 ms (**100% compliant with 60s SLA**).
- **Causal Impact (ITT)**: Evaluated across randomized arms in `sample_run/impact_report.md`.
