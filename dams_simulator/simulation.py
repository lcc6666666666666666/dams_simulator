"""调度器对比所用的离散事件仿真核心。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Sequence

from .models import FrameBlock, PathConfig, PathState, TransmissionRecord
from .scheduler import (
    BaseScheduler,
    DAMSScheduler,
    MinRTTScheduler,
    RRScheduler,
    SRPTECFScheduler,
    TransmissionRequest,
)
from .net_trace import load_bandwidth_trace_from_csv
from .video_trace import clone_trace, load_airshow_trace_from_csv


@dataclass
class FrameOutcome:
  """记录单个数据块的最终状态。"""

  block_id: int
  frame_type: str
  priority: int
  size: int
  arrival_time: float
  deadline: float
  finish_time: float | None
  dropped: bool

  @property
  def met_deadline(self) -> bool:
    if self.dropped or self.finish_time is None:
      return False
    return self.finish_time <= self.deadline


@dataclass
class SimulationResult:
  """一次仿真运行的聚合指标。"""

  scheduler_name: str
  frame_outcomes: List[FrameOutcome]
  transmissions: List[TransmissionRecord]
  path_stats: Dict[str, Dict[str, float]]
  video_length: float
  frame_rate: float
  data_volume_bytes: int
  missed_frames: int
  total_sent_bytes: int
  wasted_bytes: int

  @property
  def deadline_rate(self) -> float:
    if not self.frame_outcomes:
      return 0.0
    met = sum(1 for f in self.frame_outcomes if f.met_deadline)
    return met / len(self.frame_outcomes)

  def success_rate_by_type(self) -> Dict[str, float]:
    counts: Dict[str, List[int]] = {}
    for frame in self.frame_outcomes:
      hits = counts.setdefault(frame.frame_type, [0, 0])
      hits[1] += 1
      if frame.met_deadline:
        hits[0] += 1
    return {k: v[0] / v[1] if v[1] else 0.0 for k, v in counts.items()}

  @property
  def average_bitrate(self) -> float:
    """Average bitrate (bps) from data delivered before deadlines."""
    if self.video_length <= 0:
      return 0.0
    return (self.data_volume_bytes * 8.0) / self.video_length

  @property
  def rebuffering_time(self) -> float:
    """Total stall time implied by missed deadlines."""
    if self.frame_rate <= 0:
      return 0.0
    return self.missed_frames / self.frame_rate

  @property
  def waste_ratio(self) -> float:
    """浪费的带宽占比。"""
    if self.total_sent_bytes <= 0:
      return 0.0
    return self.wasted_bytes / self.total_sent_bytes


SchedulerFactory = Callable[[], BaseScheduler]

DEFAULT_SCHEDULERS: Dict[str, SchedulerFactory] = {
    "DAMS": DAMSScheduler,
    "RR": RRScheduler,
    "MinRTT": MinRTTScheduler,
    "SRPT-ECF": SRPTECFScheduler,
}


def build_default_paths() -> List[PathConfig]:
  """Return HSDPA-based bandwidth traces for two paths."""
  car_trace = load_bandwidth_trace_from_csv("traces/car1.csv", scale=0.8201)
  bus_trace = load_bandwidth_trace_from_csv("traces/bus2.csv", scale=0.9291)
  return [
      PathConfig(path_id="Path1-Car", bandwidth_mbps=1.50, latency_ms=10, bandwidth_trace=car_trace),
      PathConfig(path_id="Path2-Bus", bandwidth_mbps=2.36, latency_ms=40, bandwidth_trace=bus_trace),
  ]


def run_simulation(
    scheduler: BaseScheduler,
    trace: Sequence[FrameBlock],
    path_configs: Sequence[PathConfig],
) -> SimulationResult:
  """在给定的帧序列和路径配置上运行一次调度器。"""
  scheduler.reset()
  blocks = clone_trace(trace)
  blocks.sort(key=lambda b: b.arrival_time)
  ready: List[FrameBlock] = []
  release_idx = 0
  total_blocks = len(blocks)

  paths = [PathState(config=cfg, bandwidth_trace=cfg.bandwidth_trace) for cfg in path_configs]
  transmissions: List[TransmissionRecord] = []

  current_time = 0.0
  while True:
    # 将当前时间以前到达的帧全部加入 ready 队列。
    new_release = False
    while release_idx < total_blocks and blocks[release_idx].arrival_time <= current_time:
      ready.append(blocks[release_idx])
      release_idx += 1
      new_release = True

    _expire_blocks(ready, current_time)

    idle_paths = [p for p in paths if p.available_time <= current_time]
    active_blocks = any((not b.dropped) and b.remaining_size > 0 for b in ready)
    pending_arrivals = release_idx < total_blocks

    if idle_paths and active_blocks:
      decisions = scheduler.select_transmissions(ready, paths, idle_paths, current_time)
      if decisions:
        for decision in decisions:
          record = _execute_request(decision, current_time)
          if record:
            transmissions.append(record)
        continue

    next_time = _next_event_time(current_time, blocks, release_idx, paths)
    if next_time is None:
      break
    if next_time <= current_time and not new_release:
      # Nothing progressed; break to avoid infinite loop.
      break
    current_time = next_time

    if not (pending_arrivals or active_blocks or any(p.available_time > current_time for p in paths)):
      break

  # Mark unfinished frames as dropped (missed deadlines).
  for block in blocks:
    if block.remaining_size > 0 and not block.dropped:
      block.dropped = True

  sim_end = max([current_time] + [p.available_time for p in paths]) if paths else current_time

  outcomes = [
      FrameOutcome(
          block_id=blk.block_id,
          frame_type=blk.frame_type,
          priority=blk.priority,
          size=blk.size,
          arrival_time=blk.arrival_time,
          deadline=blk.deadline,
          finish_time=blk.finish_time,
          dropped=blk.dropped,
      )
      for blk in blocks
  ]
  path_stats = {
      path.path_id: {
          "busy_time": path.busy_time,
          "bytes_sent": path.bytes_sent,
          "utilization": path.busy_time / max(sim_end, 1e-9),
      }
      for path in paths
  }

  frame_rate = _infer_frame_rate(blocks)
  video_length = _video_length_seconds(blocks, frame_rate)
  data_volume_bytes = sum(
      blk.size for blk in blocks if blk.finish_time is not None and blk.finish_time <= blk.deadline
  )
  missed_frames = sum(
      1
      for blk in blocks
      if blk.finish_time is None or blk.finish_time > blk.deadline
  )
  total_sent_bytes = sum(blk.size - blk.remaining_size for blk in blocks)
  wasted_bytes = sum(
      (blk.size - blk.remaining_size)
      for blk in blocks
      if blk.finish_time is None or blk.finish_time > blk.deadline
  )

  return SimulationResult(
      scheduler_name=scheduler.name,
      frame_outcomes=outcomes,
      transmissions=transmissions,
      path_stats=path_stats,
      video_length=video_length,
      frame_rate=frame_rate,
      data_volume_bytes=data_volume_bytes,
      missed_frames=missed_frames,
      total_sent_bytes=total_sent_bytes,
      wasted_bytes=wasted_bytes,
  )


def run_default_suite(
    duration_s: float = 6.0,
    fps: int = 30,
    schedulers: Dict[str, SchedulerFactory] | None = None,
) -> Dict[str, SimulationResult]:
  """使用默认 trace+路径，批量运行多种调度器。"""
  trace_path = Path(__file__).resolve().parent / "airshow_frames_ipb.csv"
  trace = load_airshow_trace_from_csv(csv_path=str(trace_path), fps=fps, deadline_slack_s=0.2)
  path_configs = build_default_paths()
  scheduler_map = schedulers or DEFAULT_SCHEDULERS
  results: Dict[str, SimulationResult] = {}
  for name, factory in scheduler_map.items():
    sim_result = run_simulation(factory(), trace, path_configs)
    results[name] = sim_result
  return results


def _expire_blocks(blocks: Iterable[FrameBlock], current_time: float) -> None:
  """将超时未完成的块标记为 drop。"""
  for block in blocks:
    if block.dropped or block.remaining_size == 0:
      continue
    if current_time > block.deadline:
      block.dropped = True


def _execute_request(decision: TransmissionRequest, current_time: float) -> TransmissionRecord | None:
  path = decision.path
  block = decision.block
  if block.dropped or block.remaining_size <= 0:
    return None
  actual_size = min(decision.size, block.remaining_size)
  if actual_size <= 0:
    return None

  start_time = max(current_time, path.available_time)
  bandwidth = path.bandwidth_at(start_time)
  if bandwidth <= 0:
    return None
  transmit_time = actual_size / bandwidth
  finish_time = start_time + path.latency_s + transmit_time

  block.remaining_size -= actual_size
  if block.start_tx_time is None:
    block.start_tx_time = start_time
  block.finish_time = max(block.finish_time or 0.0, finish_time)

  # 如果预计完成时间已超过截止时间，则标记取消，后续不再调度该块。
  if block.finish_time > block.deadline:
    block.dropped = True

  path.available_time = finish_time
  path.busy_time += transmit_time
  path.bytes_sent += actual_size

  record = TransmissionRecord(
      block_id=block.block_id,
      path_id=path.path_id,
      size=actual_size,
      start_time=start_time,
      finish_time=finish_time,
      completed_block=(block.remaining_size == 0),
  )
  path.transmissions.append(record)
  return record


def _infer_frame_rate(blocks: Sequence[FrameBlock]) -> float:
  """根据相邻帧到达时间估算帧率，用于计算卡顿。"""
  if len(blocks) < 2:
    return 30.0 if blocks else 0.0
  deltas = [
      blocks[i + 1].arrival_time - blocks[i].arrival_time
      for i in range(len(blocks) - 1)
      if (blocks[i + 1].arrival_time - blocks[i].arrival_time) > 0
  ]
  if not deltas:
    return 30.0
  min_delta = min(deltas)
  if min_delta <= 0:
    return 30.0
  return 1.0 / min_delta


def _video_length_seconds(blocks: Sequence[FrameBlock], frame_rate: float) -> float:
  """根据帧数/帧率推导视频总时长。"""
  if not blocks:
    return 0.0
  if frame_rate > 0:
    return len(blocks) / frame_rate
  start = blocks[0].arrival_time
  end = blocks[-1].arrival_time
  if end <= start:
    return len(blocks) / 30.0
  return end - start


def _next_event_time(
    current_time: float,
    blocks: Sequence[FrameBlock],
    release_idx: int,
    paths: Sequence[PathState],
) -> float | None:
  candidates: List[float] = []
  if release_idx < len(blocks):
    candidates.append(blocks[release_idx].arrival_time)
  for path in paths:
    if path.available_time > current_time:
      candidates.append(path.available_time)
  return min(candidates) if candidates else None


if __name__ == "__main__":
  results = run_default_suite()
  for name, result in results.items():
    print(f"{name}: deadline success rate={result.deadline_rate:.2%}")
    per_type = result.success_rate_by_type()
    print("  per-frame-type:", {k: f"{v:.2%}" for k, v in per_type.items()})
    print(f"  average bitrate: {result.average_bitrate / 1e6:.2f} Mbps")
    print(f"  rebuffering time: {result.rebuffering_time:.3f} s")
    print(f"  wasted bandwidth: {result.waste_ratio:.2%}")
