"""DAMS 仿真结果的可视化工具函数。"""

from __future__ import annotations

from typing import Dict, Iterable, Sequence

import matplotlib.pyplot as plt

from .simulation import FrameOutcome, SimulationResult


def plot_deadline_curves(
    results: Dict[str, SimulationResult],
    thresholds_ms: Sequence[float] | None = None,
    output_path: str | None = None,
) -> None:
  """统计不同可接受延迟阈值下的按时率曲线。"""
  thresholds = thresholds_ms or [100, 150, 200, 250, 300, 400]
  for name, result in results.items():
    rates = [_success_with_threshold(result.frame_outcomes, t) for t in thresholds]
    plt.plot(thresholds, rates, label=name)
  plt.xlabel("Acceptable end-to-end delay (ms)")
  plt.ylabel("On-time completion rate")
  plt.ylim(0, 1.05)
  plt.legend()
  plt.title("Deadline success vs. allowed delay")
  _finalize_plot(output_path)


def plot_overall_success(
    results: Dict[str, SimulationResult],
    output_path: str | None = None,
) -> None:
  """绘制各算法整体按时率柱状图。"""
  labels = list(results.keys())
  values = [results[name].deadline_rate for name in labels]
  plt.bar(labels, values, color=["#1b9e77", "#d95f02", "#7570b3", "#66a61e"])
  plt.ylabel("On-time completion rate")
  plt.ylim(0, 1.05)
  plt.title("Scheduler comparison")
  _finalize_plot(output_path)


def plot_frame_type_breakdown(
    results: Dict[str, SimulationResult],
    output_path: str | None = None,
) -> None:
  """绘制不同帧类型（I/P/B）的准时完成率。"""
  frame_types = ["I", "P", "B"]
  x = range(len(results))
  for f_type in frame_types:
    values = [results[name].success_rate_by_type().get(f_type, 0.0) for name in results]
    plt.bar([xi + 0.1 * frame_types.index(f_type) for xi in x], values, label=f_type, width=0.2)
  plt.xticks([xi + 0.1 for xi in x], list(results.keys()))
  plt.ylabel("On-time completion rate")
  plt.ylim(0, 1.05)
  plt.title("Per-frame-type success rate")
  plt.legend()
  _finalize_plot(output_path)


def _success_with_threshold(frames: Iterable[FrameOutcome], threshold_ms: float) -> float:
  successes = 0
  total = 0
  for frame in frames:
    total += 1
    if frame.finish_time is None:
      continue
    latency = (frame.finish_time - frame.arrival_time) * 1000.0
    if not frame.dropped and latency <= threshold_ms:
      successes += 1
  return successes / total if total else 0.0


def _finalize_plot(output_path: str | None) -> None:
  plt.tight_layout()
  if output_path:
    plt.savefig(output_path, dpi=150)
    plt.close()
  else:
    plt.show()
