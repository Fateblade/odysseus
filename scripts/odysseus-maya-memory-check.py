#!/usr/bin/env python3
"""Refuse startup when available host RAM is below the experimental safety margin."""

import argparse
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--minimum-available-gib", type=int, default=16)
    args = parser.parse_args()
    if not 1 <= args.minimum_available_gib <= 4096:
        parser.error("minimum available RAM must be between 1 and 4096 GiB")
    fields = dict(line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines())
    available = int(fields["MemAvailable"].split()[0]) * 1024
    if available < args.minimum_available_gib * 1024 ** 3:
        raise SystemExit(f"Maya startup refused: {available / 1024 ** 3:.1f} GiB available; unload competing models first.")
    print(f"Before loading: {available / 1024 ** 3:.1f} GiB available. Monitor headroom during loading and inference.")


if __name__ == "__main__":
    main()
