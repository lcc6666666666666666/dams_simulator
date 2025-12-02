"""Experiment harness for DAMS deep-dive scenarios."""

from __future__ import annotations

from typing import Dict, Iterable, List, Sequence

from .net_trace import constant_bandwidth_trace, load_bandwidth_trace_from_csv, periodic_bandwidth_trace
from .simulation import SimulationResult, run_simulation
from .video_trace import load_airshow_trace_from_csv
from .models import PathConfig, FrameBlock
from .scheduler import DAMSScheduler, DAMSConservativeScheduler, DEMSScheduler

DEEPDIVE_SCHEDULERS = {
    "DAMS": DAMSScheduler,
    "DAMS-C": DAMSConservativeScheduler,
    "DEMS": DEMSScheduler,
}


def _print_results(title: str, results: Dict[str, SimulationResult]) -> None:
  print(f"== {title} ==")
  for name, res in results.items():
    print(
        f"{name}: deadline={res.deadline_rate:.2%}, "
        f"bitrate={res.average_bitrate / 1e6:.2f} Mbps, "
        f"rebuffer={res.rebuffering_time:.3f} s, waste={res.waste_ratio:.2%}"
    )


def run_rtt_sweep(
    trace: Sequence[FrameBlock] | None = None,
    schedulers: Iterable[str] | None = None,
) -> Dict[int, Dict[str, SimulationResult]]:
  trace = list(trace) if trace is not None else load_airshow_trace_from_csv(
      csv_path="airshow_frames_ipb.csv", deadline_slack_s=0.2
  )
  sched_map = _resolve_schedulers(schedulers)
  rtt2_values = [40, 80, 120, 160, 200]
  sweep_results: Dict[int, Dict[str, SimulationResult]] = {}
  for rtt2 in rtt2_values:
    paths = [
        PathConfig(path_id="P1", bandwidth_mbps=2.0, latency_ms=10, bandwidth_trace=constant_bandwidth_trace(2.0)),
        PathConfig(path_id="P2", bandwidth_mbps=3.0, latency_ms=rtt2, bandwidth_trace=constant_bandwidth_trace(3.0)),
    ]
    result = {name: run_simulation(factory(), trace, paths) for name, factory in sched_map.items()}
    sweep_results[rtt2] = result
    _print_results(f"RTT2={rtt2} ms", result)
  return sweep_results


def run_bandwidth_sweep(
    trace: Sequence[FrameBlock] | None = None,
    schedulers: Iterable[str] | None = None,
) -> Dict[str, Dict[str, SimulationResult]]:
  trace = list(trace) if trace is not None else load_airshow_trace_from_csv(
      csv_path="airshow_frames_ipb.csv", deadline_slack_s=0.2
  )
  sched_map = _resolve_schedulers(schedulers)
  combos = [
      (3.0, 3.5),
      (2.5, 3.0),
      (2.0, 2.5),
      (1.5, 2.0),
      (1.0, 1.5),
      (0.5, 1.0),
  ]
  sweep_results: Dict[str, Dict[str, SimulationResult]] = {}
  for bw1, bw2 in combos:
    key = f"{bw1}-{bw2}"
    paths = [
        PathConfig(path_id="P1", bandwidth_mbps=bw1, latency_ms=10, bandwidth_trace=constant_bandwidth_trace(bw1)),
        PathConfig(path_id="P2", bandwidth_mbps=bw2, latency_ms=20, bandwidth_trace=constant_bandwidth_trace(bw2)),
    ]
    result = {name: run_simulation(factory(), trace, paths) for name, factory in sched_map.items()}
    sweep_results[key] = result
    _print_results(f"BW1={bw1} Mbps, BW2={bw2} Mbps", result)
  return sweep_results


def run_dynamic_sweep(
    trace: Sequence[FrameBlock] | None = None,
    schedulers: Iterable[str] | None = None,
) -> Dict[str, Dict[str, SimulationResult]]:
  trace = list(trace) if trace is not None else load_airshow_trace_from_csv(
      csv_path="airshow_frames_ipb.csv", deadline_slack_s=0.2
  )
  sched_map = _resolve_schedulers(schedulers)
  rtt_pairs = [(10, 40), (10, 80)]
  sweep_results: Dict[str, Dict[str, SimulationResult]] = {}

  # Two dynamic settings (high/low toggling every 1s for period 2s).
  dynamic_settings = [
      (("Dyn1-P1", 3.0, 1.5), ("Dyn1-P2", 2.5, 1.0)),
      (("Dyn2-P1", 2.5, 1.0), ("Dyn2-P2", 2.0, 0.5)),
  ]

  for idx, ((id1, high1, low1), (id2, high2, low2)) in enumerate(dynamic_settings, start=1):
    for rtt1, rtt2 in rtt_pairs:
      key = f"dyn{idx}-rtt{rtt1}-{rtt2}"
      paths = [
          PathConfig(
              path_id=id1,
              bandwidth_mbps=high1,
              latency_ms=rtt1,
              bandwidth_trace=periodic_bandwidth_trace(high1, low1, period_s=2.0, cycles=30),
          ),
          PathConfig(
              path_id=id2,
              bandwidth_mbps=high2,
              latency_ms=rtt2,
              bandwidth_trace=periodic_bandwidth_trace(high2, low2, period_s=2.0, cycles=30),
          ),
      ]
      result = {name: run_simulation(factory(), trace, paths) for name, factory in sched_map.items()}
      sweep_results[key] = result
      _print_results(f"Dynamic setting {idx}, RTT=({rtt1},{rtt2})", result)
  return sweep_results


def _resolve_schedulers(names: Iterable[str] | None) -> Dict[str, type]:
  if names is None:
    return DEEPDIVE_SCHEDULERS
  resolved: Dict[str, type] = {}
  for n in names:
    if n not in DEEPDIVE_SCHEDULERS:
      continue
    resolved[n] = DEEPDIVE_SCHEDULERS[n]
  return resolved


if __name__ == "__main__":
  # Run all deep-dive sweeps.
  run_rtt_sweep()
  run_bandwidth_sweep()
  run_dynamic_sweep()
