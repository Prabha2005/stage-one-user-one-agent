# System Architecture: One User, One Agent

This document details the architectural design, trust boundaries, state ownership, verification separation, and failure recovery mechanisms of the STAGE Autonomous Viewer Lifecycle Agent Platform.

---

## 1. Architectural Philosophy & Ownership Model

The system is built on the principle of **One Agent per Viewer**:
- **Single Owner**: Exactly one dedicated `ViewerAgent` instance owns the lifecycle goal, active plan, decision audit trail, and historical memory for each user.
- **No Competing Subsystems**: Title recommenders, marketing push queues, and churn-prevention jobs do not act independently. All actions flow through the dedicated viewer agent.
- **Deterministic Core with Model Boundary**: Decision planning and safety verification are deterministic, rule-based, and auditable. AI model outputs are treated as probabilistic advisory hints, never as authoritative evidence or execution approval.

```
                           +---------------------------+
                           |  Inbound Trigger / Event  |
                           +-------------+-------------+
                                         |
                                         v
                           +-------------+-------------+
                           |   Fact & Context Resolver |
                           |  (Precedence, Freshness)  |
                           +-------------+-------------+
                                         |
                                         v
                           +-------------+-------------+
                           |        Goal Planner       |
                           |    (Lifecycle Strategy)   |
                           +-------------+-------------+
                                         |
                                         v
                           +-------------+-------------+
                           |   Intervention Planner    |
                           | (Home Page, Msg, Abstain) |
                           +-------------+-------------+
                                         |
                                         v
                     =========================================
                     [ TRUST BOUNDARY: INDEPENDENT VERIFIER ]
                     =========================================
                                         |
                       +-----------------+-----------------+
                       |                                   |
                  ( Approved )                        ( Rejected )
                       |                                   |
                       v                                   v
             +---------+----------+              +---------+----------+
             | Published Decision |              | Safe Abstention    |
             |  & Action Queue    |              | (NO_ACTION Logged) |
             +--------------------+              +--------------------+
```

---

## 2. Component Breakdown

### 2.1 Fact Resolver & Provenance Engine (`src/agent/fact_resolver.py`)
- **Provenance by Construction**: Every fact attached to a user carries its `source` (`event:<id>`, `profile:<snapshot_at>`, `app_setting`), `timestamp`, and calculated `freshness_s`.
- **Precedence Hierarchy**:
  1. **Dialect**: `app_setting` (what the client player will actually query and render) > `campaign_deeplink` > `profiles_snapshot.csv` (1-day stale).
  2. **Subscription**: Live billing events (`purchase_completed`, `subscription_lapsed`, `subscription_renewed`) strictly override warehouse snapshot values.
  3. **Consent**: Recent `notification_opt_out` events strictly override older snapshot opt-ins.
- **Conflict Visibility**: Conflicting sources are never silently overwritten. All competing signals are stored in the user state and recorded in `decision.dialect.conflicts`.
- **Offline Buffering Defense**: Events with transit/buffering delays > 600s (`sent_at - occurred_at > 600s` or `received_at - occurred_at > 600s`) are classified as offline-buffered and disqualified from triggering real-time interactive actions.

### 2.2 Goal Selector (`src/agent/goal_selector.py`)
- Evaluates candidate goals G01–G12 against verifiable evidence.
- **Unmeasurable Goals Disqualified**:
  - `G10` (`meaningful_engagement_7d`): Rejected because `success.event` is `null`.
  - `G11` (`tv_activation_14d`): Rejected because `tv_app_open` does not exist in the mobile event stream.
- **Handling Underspecified Goals**:
  - `G07` (`resume_series_3d`): Assumes the user started a multi-episode title, has unwatched episodes remaining in `episodes.csv`, and has had >= 72 hours of inactivity on that title.
  - `G12` (`return_after_absence_7d`): Assumes >= 14 days elapsed between prior server-received `app_open` and the trigger.
- **Dynamic Rule Corrections**: Applies operational rule updates (e.g., G03 success criteria changing from `app_open` to `play_started`) dynamically to open goals without requiring full reprocessing.

### 2.3 Intervention Planner (`src/agent/intervention_planner.py`)
- Formulates proportionate interventions:
  - **Personalized Home Page**: Up to 6 rows, 3–10 titles per row, exactly 1 hero title, unique `content_slug` per page, and dialect-matched available content.
  - **Push / WhatsApp**: Selected only when triggered outside quiet hours with verified consent and frequency cap availability.
  - **First-Class Abstention (NO_ACTION)**: Deliberate choice when user is in holdout, trigger is stale, quiet hours apply, or evidence is insufficient. Always includes an explicit `no_action_reason`.

### 2.4 Independent Safety Verifier (`src/agent/verifier.py`)
- **Strict Separation**: Operates completely independently of the planning and model layers.
- **Veto Power**: The verifier can reject or downgrade any proposed action.
- **Verification Gates**:
  1. `holdout_safety`: Confirms holdout arm users receive zero actions.
  2. `eligibility_verification`: Cross-checks goal claims against durable state facts to catch unsupported model claims.
  3. `dialect_match`: Ensures action dialect matches app setting and all titles belong to that dialect.
  4. `title_availability`: Verifies availability window (`available_from <= now < available_until`) and excludes withdrawn titles.
  5. `consent`: Validates channel-specific opt-in.
  6. `quiet_hours`: Enforces 22:00–08:00 IST blackout.
  7. `frequency_cap`: Enforces maximum of 1 message per IST calendar day across push and WhatsApp combined.
  8. `copy_policy`: Checks push length (<=40 title, <=120 body), forbidden phrases, and approved WhatsApp template IDs.

### 2.5 Model Provider & Replay Faults (`src/models/model_provider.py`)
- Enforces strict SLA budget (10s call timeout, max 8 model calls per decision).
- Handles simulated and real service degradation:
  - HTTP 503 Service Unavailable -> Caught instantly, logs warning, falls back to deterministic rule planner.
  - Degraded Read Latency / Timeouts -> Handled within 10s budget, triggers safe fallback.
  - Injected Faults (`unsupported_claim`, `malformed_output`, `timeout`) -> Safely parsed or vetoed by verifier.

### 2.6 Durable Memory (`src/store/viewer_state.py`)
- State stored durably in `sample_run/viewer_state/<user_id>.json`.
- Contains full event history, session records, play progress, active goal, decision audit trail, and behavioral learnings.

---

## 3. Real-Time Latency & Concurrency

- **Measured Performance**: Decision latency mean < 25 ms, p99 < 50 ms.
- **Budget SLA**: 100% of decisions published well within the 60-second limit.
- **Scaling Architecture for 10M Users & 40 Dialects**:
  - User State Partitioning: Key-value or document store partitioned by `user_id` (e.g., DynamoDB/ScyllaDB with Redis read cache).
  - Stream Processing: Kafka or Redpanda delivering triggers partitioned by user ID to ensure sequential consistency per viewer.
  - Local In-Memory Catalogue: Pre-indexed title arrays grouped by `dialect` and `genre`, updated via Change Data Capture (CDC) on title availability changes.
  - Sub-50ms Deterministic Execution: Stateless agent worker pool capable of processing 50,000+ app opens per second horizontally.

---

## 4. Failure Recovery & Trust Boundaries

| Boundary | Failure Mode | Mitigation & Recovery |
|---|---|---|
| **Model Service** | Timeout or HTTP 503 outage | Catch exception immediately; mark `fallback_used: true`; switch to deterministic rule engine; zero latency spike |
| **Model Output** | Hallucinated eligibility claim | Independent verifier validates against user fact store; rejects claim; records `unsupported_claim` |
| **Catalogue Content** | Adversarial prompt injection | Sanitization regex removes system prompts; treats all catalogue text as passive content |
| **Network / Mobile** | Offline buffered events | Detect delay via `(sent_at - occurred_at) > 600s`; flag as stale; abstain from real-time action |
| **Catalog Operations** | Title withdrawn mid-day | `Catalogue.withdraw_title()` updates availability timestamp immediately; verifier blocks title in subsequent actions |
