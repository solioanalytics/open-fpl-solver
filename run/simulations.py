import argparse
import json
import re
import time
from concurrent.futures import ProcessPoolExecutor

from binary_file_generator import generate_binary_files
from solve import solve_regular

from paths import DATA_DIR
from utils import load_settings


def get_user_input():
    print("Remember to delete results folder before running simulations")
    runs = int(input("How many simulations would you like to run? "))
    processes = int(input("How many processes you want to run in parallel? "))
    use_binaries = input("Use binaries (y or n)? ")
    return runs, processes, use_binaries


def get_options_from_args(options):
    runs = options.get("count", 1)
    processes = options.get("processes", 1)
    use_binaries = options.get("use_binaries", "n")
    return runs, processes, use_binaries


def setup_binary_files():
    settings = load_settings()
    if settings.get("generate_binary_files"):
        print("Generating binary files")
        binary_fixture_settings = settings.get("binary_fixture_settings", {})
        if not binary_fixture_settings:
            raise ValueError("Your `binary_fixture_settings` setting is empty!")
        file_path = DATA_DIR / "original.csv"
        generate_binary_files(file_path, binary_fixture_settings)
    return settings


def run_simulations_with_binaries(runs, processes, options):
    """Run simulations using binary files"""
    print("Using binary config for simulations")
    settings = setup_binary_files()

    weights = settings.get("binary_file_weights", {})
    total_weights = sum(weights.values())

    for binary, weight in weights.items():
        scaled_weight = weight / total_weights
        print(f"Binary file {binary} weight scaled from {weight} to {scaled_weight:.2f}")
        weighted_runs = round(scaled_weight * runs)

        print(f"Running {weighted_runs} simulations for binary file {binary}")

        start = time.time()

        runtime_options = options.get("runtime_options", {})
        all_jobs = [{"run_no": str(i + 1), "randomized": True, "datasource": binary.rstrip(".csv"), **runtime_options} for i in range(weighted_runs)]
        with ProcessPoolExecutor(max_workers=processes) as executor:
            list(executor.map(solve_regular, all_jobs))
        print(f"\nTotal time taken is {(time.time() - start) / 60:.2f} minutes")


def run_simulations_standard(runs, processes, options):
    start = time.time()
    runtime_options = options.get("runtime_options", {})
    all_jobs = [{"run_no": str(i + 1), "randomized": True, **runtime_options} for i in range(runs)]
    with ProcessPoolExecutor(max_workers=processes) as executor:
        list(executor.map(solve_regular, all_jobs))
    print(f"\nTotal time taken is {(time.time() - start) / 60:.2f} minutes")


def run_sensitivity(options=None):
    if options is None or "count" not in options:
        runs, processes, use_binaries = get_user_input()
    else:
        runs, processes, use_binaries = get_options_from_args(options)

    # if use_binaries is set, loop through binary_files dict in settings
    # and set number of sim run for each binary based on provided weights
    if use_binaries.lower() == "y":
        run_simulations_with_binaries(runs, processes, options)
    else:
        run_simulations_standard(runs, processes, options)


def parse_unknown_arguments(unknown):
    """Parse unknown command line arguments and convert them to runtime options"""
    runtime_options = {}
    i = 0
    while i < len(unknown):
        if unknown[i].startswith("--"):
            key = unknown[i][2:]  # Remove -- prefix
            if i + 1 < len(unknown) and not unknown[i + 1].startswith("--"):
                value = unknown[i + 1]
                if value.isdigit():
                    runtime_options[key] = int(value)
                else:
                    try:
                        runtime_options[key] = float(value)
                    except ValueError:
                        if value[0] in "[{":
                            try:
                                runtime_options[key] = json.loads(value)
                            except json.JSONDecodeError:
                                runtime_options[key] = json.loads(value.replace("'", '"'))
                        else:
                            runtime_options[key] = value
                i += 2
            else:
                runtime_options[key] = True
                i += 1
        else:
            i += 1
    return runtime_options


if __name__ == "__main__":
    try:
        parser = argparse.ArgumentParser(description="Run sensitivity analysis")
        parser.add_argument("--no", type=int, help="Number of runs")
        parser.add_argument("--parallel", type=int, help="Number of parallel runs")
        parser.add_argument("--use_binaries", type=str, help="Do you want to use binaries? (y/n)")

        # Parse known arguments first
        args, unknown = parser.parse_known_args()

        options = {}
        if args.no:
            options["count"] = args.no
        if args.parallel:
            options["processes"] = args.parallel
        if args.use_binaries:
            options["use_binaries"] = args.use_binaries

        # Parse unknown arguments and add them to runtime_options
        options["runtime_options"] = parse_unknown_arguments(unknown)

    except Exception:
        options = None

    # Clear command line arguments to prevent them from being passed to solve_regular
    import sys

    sys.argv = [sys.argv[0]]

    run_sensitivity(options)
