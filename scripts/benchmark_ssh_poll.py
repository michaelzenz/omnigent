#!/usr/bin/env python3
"""Benchmark remote Codex SSH polling with the global multiplexed pool."""

from __future__ import annotations

import argparse
import asyncio
import shlex
import statistics
import time

from omnigent.ssh_connections_store import read_ssh_connections
from omnigent.ssh_remote import (
    ssh_remote_codex_rollouts,
    ssh_remote_file_size,
    ssh_remote_rollout_to_tempfile,
    ssh_run,
)
from omnigent.ssh_session import SshSession, get_ssh_pool, reset_ssh_pool_for_tests, shutdown_ssh_pool


async def _time_call(label: str, coro) -> float:
    started = time.perf_counter()
    result = await coro
    elapsed = time.perf_counter() - started
    print(f"  {label}: {elapsed:.3f}s")
    return elapsed, result


async def _benchmark_multiplexed(profile, *, cycles: int) -> list[float]:
    reset_ssh_pool_for_tests()
    timings: list[float] = []
    for index in range(cycles):
        started = time.perf_counter()
        rollouts = await ssh_remote_codex_rollouts(profile)
        elapsed = time.perf_counter() - started
        timings.append(elapsed)
        tag = "cold" if index == 0 else "warm"
        print(f"  list_rollouts [{tag}]: {elapsed:.3f}s ({len(rollouts)} files)")
    await shutdown_ssh_pool()
    return timings


async def _benchmark_non_multiplexed(profile, *, cycles: int) -> list[float]:
    """Run listing without ControlMaster (one fresh ssh per cycle)."""
    timings: list[float] = []
    session = SshSession(profile.alias)
    # Bypass pool: run via a one-off session argv without multiplex options.
    from omnigent.ssh_session import build_ssh_command, control_path_for_alias

    home = "$HOME/.codex"
    remote_cmd = (
        f"(find {home}/sessions -type f -name 'rollout-*.jsonl' -print0 2>/dev/null; "
        f"find {home}/archived_sessions -maxdepth 1 -type f -name 'rollout-*.jsonl' -print0 2>/dev/null) | "
        "while IFS= read -r -d '' path; do "
        'mtime=$(stat -c %Y "$path" 2>/dev/null || stat -f %m "$path" 2>/dev/null) || continue; '
        "printf '%s\\0%s\\0' \"$path\" \"$mtime\"; "
        "done"
    )
    remote_cmd = f"bash -lc {shlex.quote(remote_cmd)}"
    for index in range(cycles):
        started = time.perf_counter()
        import asyncio

        cmd = [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=30",
            profile.alias,
            remote_cmd,
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        elapsed = time.perf_counter() - started
        timings.append(elapsed)
        count = stdout.count(b"\0") // 2
        tag = "cold" if index == 0 else "warm"
        if proc.returncode != 0:
            print(f"  list_rollouts [{tag}] FAILED: {stderr.decode().strip()}")
        else:
            print(f"  list_rollouts [{tag}]: {elapsed:.3f}s ({count} files)")
    _ = session  # silence unused
    _ = build_ssh_command
    _ = control_path_for_alias
    return timings


async def _benchmark_full_poll_cycle(profile) -> float:
    """Simulate one ambient poll: list + stat/size + tail for tracked rollouts."""
    reset_ssh_pool_for_tests()
    started = time.perf_counter()

    rollouts = await ssh_remote_codex_rollouts(profile)
    for rollout in rollouts[:3]:
        await ssh_remote_file_size(profile, rollout.path)
        temp = await ssh_remote_rollout_to_tempfile(profile, rollout.path, byte_offset=0)
        temp.unlink(missing_ok=True)

    elapsed = time.perf_counter() - started
    await shutdown_ssh_pool()
    return elapsed


async def _benchmark_command_burst(profile, *, commands: int) -> list[float]:
    reset_ssh_pool_for_tests()
    timings: list[float] = []
    for index in range(commands):
        started = time.perf_counter()
        await ssh_run(profile, "true")
        elapsed = time.perf_counter() - started
        timings.append(elapsed)
        tag = "cold" if index == 0 else "warm"
        print(f"  ssh true [{tag}]: {elapsed:.3f}s")
    await shutdown_ssh_pool()
    return timings


def _summarize(label: str, timings: list[float]) -> None:
    if not timings:
        return
    warm = timings[1:] if len(timings) > 1 else []
    print(f"\n{label} summary:")
    print(f"  cold:  {timings[0]:.3f}s")
    if warm:
        print(f"  warm:  min={min(warm):.3f}s  median={statistics.median(warm):.3f}s  max={max(warm):.3f}s")


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--alias", default=None, help="SSH config Host alias (default: first codex_remote profile)")
    parser.add_argument("--cycles", type=int, default=5, help="Poll cycles to measure")
    args = parser.parse_args()

    profiles = [p for p in read_ssh_connections() if p.codex_remote]
    if args.alias:
        profiles = [p for p in profiles if p.alias == args.alias]
    if not profiles:
        raise SystemExit("No codex_remote SSH profiles found in ~/.omnigent/config.yaml")

    profile = profiles[0]
    print(f"Target: {profile.label} ({profile.alias})")
    print(f"Pool: {get_ssh_pool().__class__.__name__} @ ~/.omnigent/ssh/control/")
    print()

    print("=== Command burst (ssh true) ===")
    burst = await _benchmark_command_burst(profile, commands=args.cycles)
    _summarize("Command burst", burst)

    print("\n=== List rollouts (multiplexed pool) ===")
    multiplexed = await _benchmark_multiplexed(profile, cycles=args.cycles)
    _summarize("Multiplexed list", multiplexed)

    print("\n=== List rollouts (no multiplex, fresh ssh each cycle) ===")
    plain = await _benchmark_non_multiplexed(profile, cycles=args.cycles)
    _summarize("Plain list", plain)

    print("\n=== Simulated full poll cycle (list + size + download top 3) ===")
    cycle_s = await _benchmark_full_poll_cycle(profile)
    print(f"  full_cycle: {cycle_s:.3f}s")

    if multiplexed and plain:
        warm_multiplex = statistics.median(multiplexed[1:]) if len(multiplexed) > 1 else multiplexed[0]
        warm_plain = statistics.median(plain[1:]) if len(plain) > 1 else plain[0]
        speedup = warm_plain / warm_multiplex if warm_multiplex > 0 else 0
        print(f"\nWarm list speedup (multiplex vs plain): {speedup:.1f}x")


if __name__ == "__main__":
    asyncio.run(main())
