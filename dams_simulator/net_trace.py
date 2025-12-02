"""Utilities for loading bandwidth traces from CSV logs."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import List

from .models import BandwidthTrace


def load_bandwidth_trace_from_csv(path: str, scale: float = 1.0) -> BandwidthTrace:
  """Load an HSDPA bandwidth trace and scale it to the desired Mbps."""
  file_path = Path(path)
  if not file_path.is_absolute():
    file_path = Path(__file__).resolve().parent / file_path

  times: List[float] = []
  bandwidths: List[float] = []

  with file_path.open("r", encoding="utf-8", newline="") as f:
    reader = csv.reader(f)
    header_checked = False
    for row in reader:
      if not row:
        continue
      if not header_checked:
        try:
          float(row[0])
        except ValueError:
          header_checked = True
          continue
        header_checked = True
      try:
        time_s = float(row[0])
        bw_mbps = float(row[1]) * scale
      except (ValueError, IndexError):
        continue
      times.append(time_s)
      bandwidths.append(bw_mbps * 1_000_000 / 8.0)

  return BandwidthTrace(times=times, bandwidth_Bps=bandwidths)


def constant_bandwidth_trace(bw_mbps: float) -> BandwidthTrace:
  """Create a degenerate trace representing constant bandwidth."""
  return BandwidthTrace(times=[0.0], bandwidth_Bps=[bw_mbps * 1_000_000 / 8.0])


def periodic_bandwidth_trace(
    high_mbps: float,
    low_mbps: float,
    period_s: float = 2.0,
    cycles: int = 10,
    start_high: bool = True,
) -> BandwidthTrace:
  """Generate a simple square-wave bandwidth trace."""
  times: List[float] = []
  values: List[float] = []
  current_time = 0.0
  current_high = start_high
  for _ in range(cycles):
    times.append(current_time)
    values.append((high_mbps if current_high else low_mbps) * 1_000_000 / 8.0)
    current_time += period_s / 2.0
    current_high = not current_high
    times.append(current_time)
    values.append((high_mbps if current_high else low_mbps) * 1_000_000 / 8.0)
    current_time += period_s / 2.0
    current_high = not current_high
  return BandwidthTrace(times=times, bandwidth_Bps=values)
