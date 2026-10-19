# Known Limitations & Scoping Decisions

This document outlines architectural choices and features that were intentionally scoped or left incomplete for the take-home submission, along with the engineering rationale.

---

## 1. Single-Node Replay vs. Distributed Streaming Architecture
- **Current Implementation**: The replay harness operates on a single process, sorting triggers and live events chronologically and processing each viewer sequentially.
- **Why It Was Left Incomplete**: The synthetic data pack (5,000 users, 2,769 triggers) fits comfortably in memory. Implementing a full distributed cluster (e.g. Apache Kafka with Flink and Cassandra) would introduce unnecessary DevOps complexity without improving decision quality or measurement honesty.
- **Production Path**: The design isolates `ViewerState` so that user partitions can be distributed across Kafka partitions and state stores without architectural redesign.

---

## 2. In-App Modal Messaging Queue
- **Current Implementation**: Real-time app open triggers prioritize `HOME_PAGE` layout personalization. In-app popups/banners are supported in the verification rules but rarely dispatched during app-open triggers.
- **Why It Was Left Incomplete**: Overwhelming users with modal dialogs on app open degrades user experience and increases churn. A full modal queue with dismiss/snooze mechanics was deprioritized in favor of seamless home page personalization.

---

## 3. Heuristic / Rule-Based Content Ranking vs. Deep Collaborative Filtering
- **Current Implementation**: Home page rows are constructed using user genre history, series progress, release recency, and dialect popularity.
- **Why It Was Left Incomplete**: The assignment brief explicitly emphasized that **this is not a recommendation system competition**. A complex neural recommender requires large training infrastructure, increases latency by orders of magnitude, and obscures causal attribution. Sub-50ms deterministic selection ensures 100% SLA compliance.

---

## 4. Cross-Channel Churn Fallbacks for Opted-Out Users
- **Current Implementation**: If a user has opted out of push and WhatsApp, or is in quiet hours, the system abstains (`NO_ACTION`) rather than attempting alternative channels.
- **Why It Was Left Incomplete**: Respecting user privacy and consent boundaries is paramount. Attempting aggressive fallback channels (e.g. SMS spam) violates the platform messaging policy.

---

## 5. TV App & Unmeasurable Goals Exclusion
- **Current Implementation**: Goal `G10` (`meaningful_engagement_7d`) has a null success event, and Goal `G11` (`tv_activation_14d`) requires `tv_app_open` events that do not exist in the mobile event stream. Both goals are systematically disqualified.
- **Why It Was Left Incomplete**: It is mathematically impossible to measure causal uplift on unobservable events. Rather than guessing, the agent explicitly documents why these goals cannot be chosen.
