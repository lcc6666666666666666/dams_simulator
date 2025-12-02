"""DAMS 仿真所需的核心数据结构。"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class FrameBlock:
  """表示一个待传输的视频帧/数据块。

  除运行时的传输字段外，其余属性在仿真期间保持不变。
  """

  block_id: int
  frame_type: str
  size: int
  priority: int
  deadline: float
  arrival_time: float
  remaining_size: int
  start_tx_time: Optional[float] = None
  finish_time: Optional[float] = None
  dropped: bool = False

  def clone(self) -> "FrameBlock":
    """生成独立副本，使不同调度算法可复用同一流量轨迹。"""
    return FrameBlock(
        block_id=self.block_id,
        frame_type=self.frame_type,
        size=self.size,
        priority=self.priority,
        deadline=self.deadline,
        arrival_time=self.arrival_time,
        remaining_size=self.size,
    )

  @property
  def completed(self) -> bool:
    return self.remaining_size == 0 and not self.dropped


class BandwidthTrace:
  """描述网络路径上带宽随时间变化的轨迹。"""

  def __init__(self, times: list[float], bandwidth_Bps: list[float]):
    if len(times) != len(bandwidth_Bps):
      raise ValueError("times and bandwidth arrays must be same length")
    self.times = times
    self.bandwidth_Bps = bandwidth_Bps

  def at(self, t: float) -> float:
    """返回时间 t 时刻的带宽（Bps），若超出范围则取最近的端点值。"""
    if not self.times:
      return 0.0
    idx = bisect_right(self.times, t) - 1
    if idx < 0:
      idx = 0
    if idx >= len(self.bandwidth_Bps):
      idx = len(self.bandwidth_Bps) - 1
    return self.bandwidth_Bps[idx]


@dataclass
class PathConfig:
  """网络路径的静态配置。"""

  path_id: str
  bandwidth_mbps: float
  latency_ms: float
  bandwidth_trace: BandwidthTrace | None = None

  @property
  def bandwidth_Bps(self) -> float:
    return self.bandwidth_mbps * 1_000_000 / 8.0

  @property
  def latency_s(self) -> float:
    return self.latency_ms / 1000.0


@dataclass
class PathState:
  """仿真中单条路径的运行时状态。"""

  config: PathConfig
  bandwidth_trace: BandwidthTrace | None = None
  available_time: float = 0.0
  busy_time: float = 0.0
  bytes_sent: int = 0
  transmissions: list["TransmissionRecord"] = field(default_factory=list)

  @property
  def path_id(self) -> str:
    return self.config.path_id

  @property
  def bandwidth_Bps(self) -> float:
    return self.config.bandwidth_Bps

  @property
  def latency_s(self) -> float:
    return self.config.latency_s

  def bandwidth_at(self, t: float) -> float:
    trace = self.bandwidth_trace or self.config.bandwidth_trace
    if trace:
      return trace.at(t)
    return self.config.bandwidth_Bps


@dataclass
class TransmissionRecord:
  block_id: int
  path_id: str
  size: int
  start_time: float
  finish_time: float
  completed_block: bool
