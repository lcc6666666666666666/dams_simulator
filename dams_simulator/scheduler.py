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
      if block.deadline <= current_time:
        continue
      allocations = self._allocate_with_deadline(block, idle_paths, current_time)
      if allocations:
        return allocations
      self._cancel_victim(prioritized, block)
    return []

  @staticmethod
  def _normalized_profit(block: FrameBlock) -> float:
    remaining = max(1, block.remaining_size)
    return block.priority / remaining

  def _cancel_victim(self, ready_blocks: Sequence[FrameBlock], current_block: FrameBlock) -> None:
    candidates = [b for b in ready_blocks if not b.dropped and b is not current_block]
    if not candidates:
      return
    victim = min(candidates, key=self._normalized_profit)
    victim.dropped = True

  def _allocate_with_deadline(
      self, block: FrameBlock, idle_paths: Sequence[PathState], current_time: float
  ) -> List[TransmissionRequest] | None:
    """按带宽比例拆分，尝试找到共同发送时长不超 deadline 的路径子集。"""
    candidates: list[tuple[PathState, float, float]] = []
    for path in idle_paths:
      start_time = max(current_time, path.available_time)
      bw = path.bandwidth_at(start_time)
      if bw <= 0:
        continue
      t_max = block.deadline - start_time - path.latency_s
      if t_max <= 0:
        continue
      candidates.append((path, bw, t_max))
    if not candidates:
      return None

    # 先看全部路径在各自窗口内的总容量是否足够
    feasible_cap = sum(bw * t_max for _, bw, t_max in candidates)
    if feasible_cap < block.remaining_size:
      return None

    # 迭代：以最小窗口为共用窗口，若无法完成则移除窗口最小的路径后重试
    while candidates:
      t_common = min(t_max for _, _, t_max in candidates)
      total_bw = sum(bw for _, bw, _ in candidates)
      if total_bw <= 0:
        return None
      send_duration = block.remaining_size / total_bw
      finish_times = [
          max(current_time, p.available_time) + p.latency_s + send_duration
          for (p, _, _) in candidates
      ]
      if send_duration <= t_common + 1e-9 and max(finish_times) <= block.deadline:
        ordered = sorted(candidates, key=lambda x: (x[0].latency_s, -x[1]))
        proportions: List[int] = []
        for _, bw, _ in ordered:
          portion = int(block.remaining_size * (bw / total_bw)) if bw > 0 else 0
          proportions.append(portion)
        if sum(proportions) == 0 and proportions:
          proportions[0] = block.remaining_size
        else:
          diff = block.remaining_size - sum(proportions)
          if proportions and diff != 0:
            proportions[0] += diff
        allocations: List[TransmissionRequest] = []
        for (path, _, _), portion in zip(ordered, proportions):
          if portion <= 0:
            continue
          allocations.append(TransmissionRequest(block=block, path=path, size=portion))
        return allocations if allocations else None
      # 丢掉共用窗口最小的那条路径，重新计算
      min_t = min(candidates, key=lambda x: x[2])[2]
      candidates = [c for c in candidates if c[2] > min_t]
    return None


class DAMSConservativeScheduler(DAMSScheduler):
  """DAMS-C: 从接收端视角力求子流“同时完成”。

  思路：找到一个最早的完成时间 T（不超过截止时间），使得各路径在 T 前的可用容量
  之和足以覆盖当前块，然后按各路径在 T 前的可用容量比例拆分，力求在接收端同时收齐。
  """

  name = "DAMS-C"

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
      finish_target = self._earliest_feasible_finish(block, idle_paths, current_time)
      if finish_target is None:
        continue
      caps = self._capacity_until_finish(idle_paths, current_time, finish_target, block.deadline)
      total_cap = sum(caps.values())
      if total_cap <= 0 or block.remaining_size > total_cap:
        continue

      size_needed = block.remaining_size
      ordered_paths = sorted(idle_paths, key=lambda p: (p.latency_s, -p.bandwidth_at(current_time)))
      proportions: List[int] = []
      for path in ordered_paths:
        cap = caps.get(path.path_id, 0.0)
        portion = int(size_needed * (cap / total_cap)) if cap > 0 else 0
        proportions.append(portion)
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

  def _earliest_feasible_finish(
      self, block: FrameBlock, paths: Sequence[PathState], current_time: float
  ) -> float | None:
    """二分搜索最早可行的完成时间（不超过截止），使容量之和覆盖块大小。"""
    start_min = min(max(current_time, p.available_time) + p.latency_s for p in paths)
    low = start_min
    high = block.deadline
    if self._capacity_sum(paths, current_time, high) < block.remaining_size:
      return None
    for _ in range(30):
      mid = (low + high) / 2
      if self._capacity_sum(paths, current_time, mid) >= block.remaining_size:
        high = mid
      else:
        low = mid
    return high

  def _capacity_sum(self, paths: Sequence[PathState], current_time: float, finish: float) -> float:
    caps = self._capacity_until_finish(paths, current_time, finish, finish)
    return sum(caps.values())

  def _capacity_until_finish(
      self, paths: Sequence[PathState], current_time: float, finish: float, deadline: float
  ) -> dict[str, float]:
    caps: dict[str, float] = {}
    target = min(finish, deadline)
    for path in paths:
      start_time = max(current_time, path.available_time)
      time_budget = target - start_time - path.latency_s
      if time_budget <= 0:
        caps[path.path_id] = 0.0
        continue
      bandwidth = path.bandwidth_at(start_time)
      caps[path.path_id] = max(0.0, bandwidth * time_budget)
    return caps


class DEMSScheduler(BaseScheduler):
  """DEMS: 关注单个块完成时间的拆分策略。"""

  name = "DEMS"

  def select_transmissions(self, ready_blocks, all_paths, idle_paths, current_time):
    ready = self._eligible_blocks(ready_blocks, current_time)
    if not ready or not idle_paths:
      return []
    # 选优先级高、deadline 早的块（单块优化）
    block = self._sorted_by_priority(ready)[0]
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
      return []
    if block.remaining_size > total_cap:
      return []
    ordered_paths = sorted(idle_paths, key=lambda p: (p.latency_s, -p.bandwidth_at(current_time)))
    size_needed = block.remaining_size
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
    return allocations
