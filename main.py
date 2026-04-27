"""
SDDaaS — Main Runner
====================
Runs all experiments sequentially:
  1. SDDaaS_Baseline.py     — SDDaaS vs Linear Search (search time + storage)
  2. SDDaaS_Demo.py         — Interactive Demo
  3. SDDaaS_Relatedwork.py  — Analytical comparison vs baseline systems
Usage:
    python main.py
"""

import runpy
import sys
import os

EXPERIMENTS = [
    ("Experiment 1: SDDaaS Baseline", "experiments/SDDaaS_Baseline.py"),
    ("Experiment 2: SDDaaS Demo", "experiments/SDDaaS_Demo.py"),
    ("Experiment 3: Related Work Comparison", "experiments/SDDaaS_Relatedwork.py"),
]

def run_experiment(label, path):
    print("\n" + "=" * 65)
    print(f"  {label}")
    print(f"  Running: {path}")
    print("=" * 65 + "\n")

    # Change working directory so relative output paths (e.g. .png) save next to the script
    original_dir = os.getcwd()
    script_dir   = os.path.dirname(os.path.abspath(path))
    os.chdir(script_dir)

    try:
        runpy.run_path(os.path.abspath(os.path.join(original_dir, path)),
                       run_name="__main__")
    except Exception as e:
        print(f"\n[ERROR] {path} failed: {e}")
        sys.exit(1)
    finally:
        os.chdir(original_dir)

    print(f"\n[✓] {label} complete.")


if __name__ == "__main__":
    print("=" * 65)
    print("  SDDaaS — Full Experiment Runner")
    print("=" * 65)

    for label, path in EXPERIMENTS:
        run_experiment(label, path)

    print("\n" + "=" * 65)
    print("  All experiments complete.")
    print("  Check the experiments/ folder for output graphs (.png)")
    print("=" * 65)
