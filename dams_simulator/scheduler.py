"""Schedulers used by the DAMS simulator."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Sequence

from .models import FrameBlock, PathState


@dataclass
class TransmissionRequest:
  """一次调度决策中某块在某条路径上发送的动作。"""
  block: FrameBlock
  path: PathState
  size: int


class BaseScheduler:
  name = "base"

  def reset(self) -> None:
    """供需要内部状态初始化的调度器重写。"""

  def select_transmissions(
      self,
      ready_blocks: Sequence[FrameBlock],
      all_paths: Sequence[PathState],
      idle_paths: Sequence[PathState],
      current_time: float,
  ) -> List[TransmissionRequest]:
    raise NotImplementedError

  # Helper filters -------------------------------------------------------

  def _sorted_by_priority(self, blocks: Iterable[FrameBlock]) -> List[FrameBlock]:
    # 优先级从高到低，deadline 更紧的放前面。
    return sorted(blocks, key=lambda b: (-b.priority, b.deadline, b.block_id))

  def _eligible_blocks(self, blocks: Sequence[FrameBlock], current_time: float) -> List[FrameBlock]:
    # 过滤掉已经过期/dropped 或传输完的块。
    return [b for b in blocks if (not b.dropped) and b.remaining_size > 0 and b.deadline > current_time]


class RRScheduler(BaseScheduler):
  name = "RoundRobin"

  def __init__(self) -> None:
    self._next_index = 0
    self._path_order: list[str] | None = None

  def reset(self) -> None:
    self._next_index = 0
    self._path_order = None

  def select_transmissions(self, ready_blocks, all_paths, idle_paths, current_time):
    ready = self._eligible_blocks(ready_blocks, current_time)
    if not ready or not idle_paths:
      return []
    # 初始化固定的路径顺序，只在路径数量变化时重建。
    if self._path_order is None or len(self._path_order) != len(all_paths):
      self._path_order = [path.path_id for path in all_paths]
      self._next_index = 0

    idle_map = {p.path_id: p for p in idle_paths}
    ordered_blocks = self._sorted_by_priority(ready)
    requests: List[TransmissionRequest] = []
    block_queue = ordered_blocks.copy()

    while block_queue:
      path = self._pick_next_idle_path(idle_map)
      if path is None:
        break
      block = block_queue.pop(0)
      requests.append(TransmissionRequest(block=block, path=path, size=block.remaining_size))
    return requests

  def _pick_next_idle_path(self, idle_map: dict[str, PathState]) -> PathState | None:
    if not idle_map or not self._path_order:
      return None
    count = len(self._path_order)
    for offset in range(count):
      idx = (self._next_index + offset) % count
      path_id = self._path_order[idx]
      path = idle_map.get(path_id)
      if path:
        self._next_index = (idx + 1) % count
        return path
    return None


class MinRTTScheduler(BaseScheduler):
  name = "MinRTT"

  def select_transmissions(self, ready_blocks, all_paths, idle_paths, current_time):
    ready = self._eligible_blocks(ready_blocks, current_time)
    if not ready or not idle_paths:
      return []
    block_queue = self._sorted_by_priority(ready)
    available_paths = list(idle_paths)
    requests: List[TransmissionRequest] = []
    while block_queue and available_paths:
      block = block_queue.pop(0)
      best_path = min(
          available_paths,
          key=lambda p: self._finish_time_estimate(p, block, current_time),
      )
      requests.append(TransmissionRequest(block=block, path=best_path, size=block.remaining_size))
      available_paths.remove(best_path)
    return requests

  @staticmethod
  def _finish_time_estimate(path: PathState, block: FrameBlock, current_time: float) -> float:
    start_time = max(current_time, path.available_time)
    bandwidth = path.bandwidth_at(start_time)
    if bandwidth <= 0:
      transmit_time = float("inf")
    else:
      transmit_time = block.remaining_size / bandwidth
    return start_time + path.latency_s + transmit_time


class SRPTECFScheduler(BaseScheduler):
  name = "SRPT-ECF"

  def select_transmissions(self, ready_blocks, all_paths, idle_paths, current_time):
    ready = self._eligible_blocks(ready_blocks, current_time)
    if not ready or not idle_paths:
      return []
    # 穷举所有 block-path 组合，选择预计最早完成的组合后再重复。
    pending_blocks = sorted(ready, key=lambda b: (b.remaining_size, b.deadline, b.block_id))
    remaining_paths = list(idle_paths)
    requests: List[TransmissionRequest] = []
    while pending_blocks and remaining_paths:
      best_choice: tuple[float, FrameBlock, PathState] | None = None
      for block in pending_blocks:
        for path in remaining_paths:
          bandwidth = path.bandwidth_at(current_time)
          if bandwidth <= 0:
            transmit_time = float("inf")
          else:
            transmit_time = block.remaining_size / bandwidth
          finish = current_time + path.latency_s + transmit_time
          if best_choice is None or finish < best_choice[0]:
            best_choice = (finish, block, path)
      if best_choice is None:
        break
      _, block, path = best_choice
      requests.append(TransmissionRequest(block=block, path=path, size=block.remaining_size))
      pending_blocks.remove(block)
      remaining_paths.remove(path)
    return requests


class DAMSScheduler(BaseScheduler):
  name = "DAMS"

  def select_transmissions(self, ready_blocks, all_paths, idle_paths, current_time):
    if not idle_paths:
      return []
    ready = self._eligible_blocks(ready_blocks, current_time)
    if not ready:
      return []
    prioritized = self._sorted_by_priority(ready)

    for block in prioritized:
      remaining_time = block.deadline - current_time
      if remaining_time <= 0:
        continue
      caps: dict[str, float] = {}
      for path in idle_paths:
        start_time = max(current_time, path.available_time)
        time_budget = block.deadline - start_time - path.latency_s
        if time_budget <= 0:
          caps[path.path_id] = 0.0
          continue
        bandwidth = path.bandwidth_at(start_time)
        caps[path.path_id] = max(0.0, bandwidth * time_budget)
      total_cap = sum(caps.values())
      if total_cap <= 0:
        continue
      if block.remaining_size > total_cap:
        continue

      size_needed = block.remaining_size
      ordered_paths = sorted(idle_paths, key=lambda p: (p.latency_s, -p.bandwidth_at(current_time)))
      proportions: List[int] = []
      for path in ordered_paths:
        cap = caps[path.path_id]
        if cap <= 0:
          proportions.append(0)
        else:
          proportions.append(int(size_needed * (cap / total_cap)))

      if sum(proportions) == 0 and proportions:
        proportions[0] = size_needed
      else:
        diff = size_needed - sum(proportions)
        if proportions and diff != 0:
          proportions[0] += diff

      allocations: List[TransmissionRequest] = []
      for path, portion in zip(ordered_paths, proportions):
        if portion <= 0:
          continue
        allocations.append(TransmissionRequest(block=block, path=path, size=portion))
      if allocations:
        return allocations
    return []
