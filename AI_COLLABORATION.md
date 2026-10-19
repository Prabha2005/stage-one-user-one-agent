# AI Collaboration Log

This log documents the role of AI coding assistants during the development of the STAGE One User, One Agent assignment, including how AI proposals were validated, corrected, and rejected.

---

## 1. Summary of AI Assistance

AI tools (including Claude, Codex, and Antigravity) were utilized as pair programming assistants for:
- Scaffolding project directory layout and boilerplate dataclasses.
- Generating mathematical formulas for proportion testing (two-sample z-test and confidence intervals).
- Identifying candidate regex patterns for prompt injection defenses in catalogue text.

However, all AI-generated logic was rigorously checked against the assignment specification, unit tested with `pytest`, and held to strict engineering standards.

---

## 2. Specific Examples of AI Output Corrected or Rejected

### Example 1: Rejecting "End-to-End LLM Prompting" for Decision Making
- **AI Proposal**: An early AI suggestion proposed feeding the viewer's entire event history into an LLM prompt to directly output the selected goal, action type, and a list of recommended title IDs in JSON format.
- **Why It Was Rejected**:
  1. *Hallucination & Policy Risk*: The model could invent title IDs, select withdrawn titles (e.g. T0001), or hallucinate subscription eligibility.
  2. *Latency Budget*: LLM API inference incurs 1.5–5.0 seconds of latency, risking the 60-second SLA during traffic spikes and consuming excessive tokens.
  3. *Violation of Core Principles*: The assignment brief explicitly stated that model output is never proof or approval.
- **Adopted Solution**: Implemented a deterministic, sub-50ms rule-based `GoalSelector` and `InterventionPlanner`, bounded by an `IndependentSafetyVerifier`. The model provider was isolated behind an advisory interface with timeout and outage fallbacks.

---

### Example 2: Correcting the Pilot Report Uplift Methodology (Selection Bias)
- **AI Proposal**: When asked to generate the impact evaluation script, an AI model mirrored the methodology of `pilot/pilot_summary_report.md`, writing code that grouped users into `served = actions_count > 0` vs `unserved`.
- **Why It Was Rejected**:
  - Comparing "served" users (who received an action) against all other users introduces severe **selection and compliance bias**. Active users who open the app and trigger the agent have a naturally higher baseline conversion rate regardless of intervention.
  - The pilot report had claimed a misleading **+22.4 percentage point uplift (40.1% vs 17.7%)** using this biased comparison.
- **Adopted Solution**: Corrected the metric to standard **Intention-to-Treat (ITT)** comparing all assigned treatment arm users vs all holdout users. This showed that the true pilot uplift was actually **-1.30 percentage points** (95% CI: [-3.76, +1.16]), completely debunking the pilot headline.

---

### Example 3: Correcting Deeplink Dialect Extraction Index
- **AI Proposal**: In `src/store/viewer_state.py`, the AI assistant generated `parts = deeplink.split("/")` and assigned `camp_d = parts[3]` for deeplinks matching `ott://c/<campaign>/<dialect>/title/<title_id>`.
- **Why It Was Rejected / Corrected**:
  - In Python, `"ott://c/diwali/d2/title/T0101".split("/")` yields:
    - Index 0: `'ott:'`
    - Index 1: `''`
    - Index 2: `'c'`
    - Index 3: `'diwali'` (Campaign name)
    - Index 4: `'d2'` (Dialect identifier)
  - Extracting index 3 caused dialect conflict resolution tests to fail.
- **Adopted Solution**: Corrected index to `parts[4]` and added automated unit tests specifically validating dialect extraction across campaign links.

---

### Example 4: Rejecting Full State Reprocessing on Mid-Replay Rule Correction
- **AI Proposal**: When the `goal_rule_corrected` control event was introduced for G03 at 08:30 UTC, the assistant suggested re-evaluating the entire historical event stream for all users to update their goal status.
- **Why It Was Rejected**:
  - Reprocessing all 5,000 users mid-day breaks real-time latency budgets, violates temporal causal boundaries, and causes redundant computations.
- **Adopted Solution**: Designed `GoalSelector.apply_rule_correction()`, which applies the updated rule dynamically only to goals that are currently `OPEN` at or after 08:30 UTC. Closed goals and past decisions remain immutable.

---

## 3. Engineering Takeaway

AI coding assistants are highly effective accelerators for boilerplate and mathematical utility functions, but architectural design, statistical honesty, trust boundaries, and safety verification must be directed and validated by the engineer.
