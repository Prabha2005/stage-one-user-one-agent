"""Data loader for the STAGE data pack."""
import csv
import json
import logging
import os
from collections import defaultdict
from typing import Dict, Generator, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


def read_jsonl(path: str) -> Generator[dict, None, None]:
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    yield json.loads(line)
                except Exception as e:
                    logger.error("Error reading JSONL line from %s: %s", path, e)


class DataLoader:
    def __init__(self, root_dir: str):
        self.root_dir = root_dir

    def load_arms(self) -> Dict[str, str]:
        arms = {}
        arms_path = os.path.join(self.root_dir, "replay", "arms.csv")
        if os.path.exists(arms_path):
            with open(arms_path, encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    arms[row["user_id"]] = row["arm"]
        return arms

    def load_profiles(self) -> Dict[str, dict]:
        """Loads profiles resolving duplicates by choosing the newest updated_at."""
        profiles = {}
        prof_path = os.path.join(self.root_dir, "data", "profiles_snapshot.csv")
        if os.path.exists(prof_path):
            with open(prof_path, encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    uid = row["user_id"]
                    existing = profiles.get(uid)
                    if existing is None or row.get("updated_at", "") > existing.get("updated_at", ""):
                        profiles[uid] = row
        return profiles

    def load_configs(self) -> Tuple[dict, dict, dict]:
        with open(os.path.join(self.root_dir, "config", "goals.json"), encoding="utf-8") as f:
            goals_cfg = json.load(f)
        with open(os.path.join(self.root_dir, "config", "interventions.json"), encoding="utf-8") as f:
            interventions_cfg = json.load(f)
        with open(os.path.join(self.root_dir, "config", "pricing.json"), encoding="utf-8") as f:
            pricing_cfg = json.load(f)
        return goals_cfg, interventions_cfg, pricing_cfg

    def stream_historical_events(self) -> Generator[dict, None, None]:
        events_path = os.path.join(self.root_dir, "data", "events.jsonl")
        yield from read_jsonl(events_path)

    def load_model_faults(self) -> Dict[str, dict]:
        """Maps trigger_event_id -> fault info."""
        faults = {}
        faults_path = os.path.join(self.root_dir, "replay", "model_faults.jsonl")
        for row in read_jsonl(faults_path):
            tid = row.get("trigger_event_id")
            if tid:
                faults[tid] = row
        return faults

    def load_control_events(self) -> List[dict]:
        ctl_path = os.path.join(self.root_dir, "replay", "control_events.jsonl")
        events = list(read_jsonl(ctl_path))
        events.sort(key=lambda x: x.get("at", ""))
        return events
