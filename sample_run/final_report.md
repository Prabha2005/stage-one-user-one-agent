# Final Engineering & Evaluation Report: One User, One Agent

**Candidate System**: STAGE Autonomous Viewer Lifecycle Agent Engine  
**Evaluation Scope**: 5,000 synthetic viewers, 30-day historical pack, replay trigger stream, 14-day outcome simulation.

---

## 1. System Overview & Core Principles

The STAGE viewer agent platform assigns a dedicated lifecycle agent to every viewer. The agent operates as a **disciplined lifecycle manager**, not a naive recommender. It solves the cold start, retention, and subscription problems of regional OTT streaming through seven rigorous steps:

1. **Understand Viewer**: Assemble facts with explicit source, timestamp, and freshness.
2. **Choose Goal**: Commit to one measurable goal from candidate goals G01-G12 with explicit assumptions, discarding unmeasurable goals.
3. **Decide Intervention**: Personalize home page layout, push, WhatsApp, or deliberately abstain (NO_ACTION).
4. **Act Within Budget**: Process decisions within <60 seconds SLA and minimal model/tool call budget.
5. **Verify Independently**: An independent safety verifier checks holdout isolation, consent, quiet hours, frequency caps, dialect alignment, and availability before any action is published.
6. **Close Loop**: Judge goal success strictly from attributable post-replay outcome events.
7. **Measure Impact Honestly**: Compare the full agent arm against randomized holdout using Intention-to-Treat (ITT).

---

## 2. Traps Identified & Handled

| Category | Specific Trap Found | How System Handled It | Verification Evidence |
|---|---|---|---|
| **Data Integrity** | 2,176 duplicate event IDs in `events.jsonl` | Deduplication on ingestion using `seen_event_ids` set | Zero duplicate event processing |
| **Data Integrity** | Stale profile snapshot (1 day old) with 30 duplicate user rows | Picked freshest row by `updated_at`; newer live events override snapshot | Fresher subscription events override snapshot |
| **Data Integrity** | 50 brand-new users in events with no profile row | Constructed viewer state from raw events (`signup_completed`) | Seamless initialization without crashes |
| **Clock / Network** | 455 offline-buffered app opens (phone delay > 600s) | Detected via `(sent_at - occurred_at) > 600s`; marked as stale trigger and returned NO_ACTION | Avoided sending real-time actions to inactive sessions |
| **Dialects** | Dialect conflicts between app setting, profile, and campaign link | Authoritative precedence: App Setting > Campaign Link > Profile. Conflicts tracked in output | Conflicts explicitly logged in `decision.dialect.conflicts` |
| **Catalogue** | Prompt injection in titles T0002 & T0522 (fake system instructions) | Sanitized descriptions and isolated content from instructions | Verifier rejected model overrides; zero policy breach |
| **Operational** | Dynamic title withdrawal (T0001 at 07:00 UTC) | Dynamically set `available_until` at event timestamp | Title never used in actions after 07:00 UTC |
| **Operational** | Dynamic G03 rule correction at 08:30 UTC | Applied new rule to all open G03 goals without re-processing all users | Versioned rule recorded in goal state |
| **Model Reliability** | Model faults: `unsupported_claim`, `malformed_output`, `timeout` | Verifier rejected unsupported claims; caught timeouts & syntax errors; graceful fallback | 100% decision SLA compliance (<60s) |
| **Measurement** | Biased pilot headline (+22.4% served vs unserved) | Recomputed randomized ITT (-1.30%) and documented selection bias | Included in impact report and final documentation |

---

## 3. Key Metrics Summary

- **Total Processed Triggers**: 2,769
- **Decisions Published / Recorded**: 2,769 (100% recorded with full audit trail)
- **Holdout Violations**: **0** (Absolute holdout isolation maintained)
- **Policy Violations (Consent, Quiet Hours, Frequency)**: **0**
- **Decision Latency**: Mean < 25 ms, p99 < 50 ms (Strict compliance with 60s SLA)
- **ITT Uplift**: Documented in `impact_report.md` with 95% Confidence Intervals.

---

## 4. Technical Discussion Highlights

1. **How does the agent decide what it knows is current enough?**  
   Every fact carries a source, timestamp, and calculated freshness. A server-received purchase event always supersedes a 1-day stale profile snapshot.

2. **When should the agent do nothing?**  
   When the viewer is in the holdout arm, when a trigger was buffered offline days ago, during quiet hours without consent, when frequency caps are reached, or when no evidence supports an eligible goal. Doing nothing is a first-class decision with an explicit recorded reason.

3. **How is causal impact proven?**  
   By strictly comparing all users in the agent arm vs all users in the holdout arm (Intention-to-Treat), avoiding the selection bias that invalidates "served vs unserved" comparisons.

4. **How would the system scale to 10M users and 40 dialects?**  
   Per-viewer state stores partitioned by user ID (e.g. DynamoDB/Cassandra or tiered Redis cache), streaming pipeline (Kafka/Flink) delivering triggers, stateless agent workers executing sub-50ms deterministic verification, and localized dialect catalogue indexes.
