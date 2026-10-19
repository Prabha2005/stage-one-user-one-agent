"""CLI for running replay, simulation, judging, and impact evaluation."""
import argparse
import logging
import os
import subprocess
import sys

from src.data.catalogue import Catalogue
from src.evaluation.impact_reporter import ImpactReporter
from src.evaluation.latency_reporter import LatencyReporter
from src.evaluation.outcome_judge import OutcomeJudge
from src.replay.harness import ReplayHarness

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("STAGE-Agent-CLI")


def cmd_replay(args):
    harness = ReplayHarness(root_dir=args.pack, output_dir=args.out)
    harness.setup()
    harness.run()
    # Also generate latency report
    LatencyReporter.generate_report(harness.latencies_ms, output_file=os.path.join(args.out, "latency_report.json"))
    logger.info("Replay completed successfully.")


def cmd_simulate(args):
    sim_script = os.path.join(args.pack, "simulator", "simulate_outcomes.py")
    cmd = [
        sys.executable,
        sim_script,
        "--pack", args.pack,
        "--actions", args.actions,
        "--out", args.out,
        "--seed", args.seed
    ]
    logger.info("Running simulator: %s", " ".join(cmd))
    res = subprocess.run(cmd, check=True)
    if res.returncode == 0:
        logger.info("Simulation completed successfully.")


def cmd_judge(args):
    cat = Catalogue(os.path.join(args.pack, "data"))
    judge = OutcomeJudge(
        pack_dir=args.pack,
        decisions_file=args.decisions,
        outcome_events_file=args.events,
        catalogue=cat
    )
    judge.judge_all(output_file=args.out)
    logger.info("Judging completed successfully.")


def cmd_report(args):
    reporter = ImpactReporter(root_dir=args.pack, sample_dir=args.sample_dir)
    reporter.generate_reports()
    logger.info("Reporting completed successfully.")


def cmd_run_all(args):
    """Executes the complete pipeline: replay -> simulate -> judge -> report in one command."""
    logger.info("=== STEP 1: RUNNING REPLAY HARNESS ===")
    harness = ReplayHarness(root_dir=args.pack, output_dir=args.out)
    harness.setup()
    harness.run()

    latency_path = os.path.join(args.out, "latency_report.json")
    LatencyReporter.generate_report(harness.latencies_ms, output_file=latency_path)

    logger.info("=== STEP 2: RUNNING SIMULATOR ===")
    sim_out = os.path.join(args.out, "sim")
    actions_path = os.path.join(args.out, "actions.jsonl")
    sim_script = os.path.join(args.pack, "simulator", "simulate_outcomes.py")
    subprocess.run([
        sys.executable,
        sim_script,
        "--pack", args.pack,
        "--actions", actions_path,
        "--out", sim_out,
        "--seed", args.seed
    ], check=True)

    logger.info("=== STEP 3: JUDGING OUTCOMES ===")
    decisions_path = os.path.join(args.out, "decisions.jsonl")
    outcome_events_path = os.path.join(sim_out, "outcome_events.jsonl")
    outcomes_path = os.path.join(args.out, "outcomes.jsonl")
    cat = Catalogue(os.path.join(args.pack, "data"))
    judge = OutcomeJudge(
        pack_dir=args.pack,
        decisions_file=decisions_path,
        outcome_events_file=outcome_events_path,
        catalogue=cat
    )
    judge.judge_all(output_file=outcomes_path)

    logger.info("=== STEP 4: GENERATING IMPACT & FINAL REPORTS ===")
    reporter = ImpactReporter(root_dir=args.pack, sample_dir=args.out)
    reporter.generate_reports()
    logger.info("=== FULL RUN-ALL PIPELINE COMPLETE ===")


def main():
    parser = argparse.ArgumentParser(description="STAGE One User, One Agent Engine CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Replay
    p_replay = subparsers.add_parser("replay", help="Run the replay harness")
    p_replay.add_argument("--pack", default=".", help="Root directory of data pack")
    p_replay.add_argument("--out", default="sample_run", help="Output directory")
    p_replay.set_defaults(func=cmd_replay)

    # Simulate
    p_sim = subparsers.add_parser("simulate", help="Run the outcome simulator")
    p_sim.add_argument("--pack", default=".", help="Root directory of data pack")
    p_sim.add_argument("--actions", default="sample_run/actions.jsonl", help="Actions file")
    p_sim.add_argument("--out", default="sample_run/sim", help="Simulator output dir")
    p_sim.add_argument("--seed", default="oua-practice", help="Simulation seed")
    p_sim.set_defaults(func=cmd_simulate)

    # Judge
    p_judge = subparsers.add_parser("judge", help="Judge goal outcomes from simulation")
    p_judge.add_argument("--pack", default=".", help="Root directory of data pack")
    p_judge.add_argument("--decisions", default="sample_run/decisions.jsonl", help="Decisions file")
    p_judge.add_argument("--events", default="sample_run/sim/outcome_events.jsonl", help="Outcome events file")
    p_judge.add_argument("--out", default="sample_run/outcomes.jsonl", help="Outcomes output file")
    p_judge.set_defaults(func=cmd_judge)

    # Report
    p_report = subparsers.add_parser("report", help="Generate impact and final reports")
    p_report.add_argument("--pack", default=".", help="Root directory of data pack")
    p_report.add_argument("--sample-dir", default="sample_run", help="Sample run directory")
    p_report.set_defaults(func=cmd_report)

    # Run-all
    p_all = subparsers.add_parser("run-all", help="Execute complete pipeline end-to-end")
    p_all.add_argument("--pack", default=".", help="Root directory of data pack")
    p_all.add_argument("--out", default="sample_run", help="Output directory")
    p_all.add_argument("--seed", default="oua-practice", help="Simulation seed")
    p_all.set_defaults(func=cmd_run_all)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
