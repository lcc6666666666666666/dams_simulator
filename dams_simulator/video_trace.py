"""根据 GOP 规则生成 IPB 帧流量轨迹。"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Sequence

from .models import FrameBlock


@dataclass(frozen=True)
class FrameSpec:
  frame_type: str
  size: int
  priority: int


DEFAULT_FRAME_SPECS = {
    "I": FrameSpec("I", 120 * 1024, 3),
    "P": FrameSpec("P", 60 * 1024, 2),
    "B": FrameSpec("B", 20 * 1024, 1),
}


def generate_video_trace(
    duration_s: float = 5.0,
    fps: int = 30,
    gop: int = 30,
    p_interval: int = 5,
    deadline_slack_s: float = 0.3,
    frame_specs: dict[str, FrameSpec] | None = None,
) -> List[FrameBlock]:
  """按固定模式生成 I/P/B 帧序列。

  Args:
    duration_s: 视频时长，单位秒。
    fps: 帧率。
    gop: GOP 大小，周期首帧视为 I 帧。
    p_interval: GOP 内 P 帧之间的帧间隔。
    deadline_slack_s: 每帧到达后允许的截止宽限。
    frame_specs: 可选的帧大小/优先级配置。

  Returns:
    已按到达时间排序的 FrameBlock 列表。
  """
  specs = frame_specs or DEFAULT_FRAME_SPECS
  num_frames = int(duration_s * fps)
  trace: List[FrameBlock] = []
  for frame_idx in range(num_frames):
    arrival_time = frame_idx / fps
    frame_type = _resolve_frame_type(frame_idx, gop=gop, p_interval=p_interval)
    spec = specs[frame_type]
    deadline = arrival_time + deadline_slack_s
    trace.append(
        FrameBlock(
            block_id=frame_idx,
            frame_type=frame_type,
            size=spec.size,
            priority=spec.priority,
            deadline=deadline,
            arrival_time=arrival_time,
            remaining_size=spec.size,
        )
    )
  return trace


def _resolve_frame_type(frame_idx: int, gop: int, p_interval: int) -> str:
  if frame_idx % gop == 0:
    return "I"
  if frame_idx % p_interval == 0:
    return "P"
  return "B"


def clone_trace(blocks: Sequence[FrameBlock]) -> List[FrameBlock]:
  """Deep copy helper so each algorithm sees identical input."""
  return [block.clone() for block in blocks]


PRIORITY_MAP = {"I": 2, "P": 1, "B": 0}


def load_airshow_trace_from_csv(
    csv_path: str,
    fps: int = 30,
    deadline_slack_s: float = 0.3,
) -> List[FrameBlock]:
  """Load a real IPB trace (AirShow) from CSV."""
  file_path = Path(csv_path)
  if not file_path.is_absolute():
    file_path = Path(__file__).resolve().parent / file_path

  trace: List[FrameBlock] = []
  with file_path.open("r", encoding="utf-8", newline="") as f:
    reader = csv.reader(f)
    for frame_idx, row in enumerate(reader):
      if not row or len(row) < 2:
        continue
      try:
        size_bytes = int(row[0])
      except ValueError:
        continue
      frame_type = row[1].strip().upper()
      if frame_type not in PRIORITY_MAP:
        continue
      priority = PRIORITY_MAP[frame_type]
      arrival_time = frame_idx / fps
      deadline = arrival_time + deadline_slack_s
      trace.append(
          FrameBlock(
              block_id=frame_idx,
              frame_type=frame_type,
              size=size_bytes,
              priority=priority,
              deadline=deadline,
              arrival_time=arrival_time,
              remaining_size=size_bytes,
          )
      )
  return trace
