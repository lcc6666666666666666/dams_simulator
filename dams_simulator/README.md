# Deadline-Aware Multipath Scheduler (DAMS) Demo

This folder provides a lightweight Python prototype that mimics the scheduler design from *Deadline-Aware Multipath Transmission for Streaming Blocks*. It focuses on multipath deadline-aware scheduling decisions rather than a full MPQUIC stack so you can quickly demo the paper's core ideas on a Windows laptop (with or without WSL).

## Quick Start

```bash
python -m venv .venv
.venv\Scripts\activate  # PowerShell: .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt  # contains matplotlib
python -m dams_simulator.simulation
```

Running `python -m dams_simulator.simulation` prints each scheduler's overall deadline success rate on a default 6-second 30 FPS video trace using two synthetic paths (WiFi-like and cellular-like links).

To visualize the comparison:

```python
from dams_simulator.simulation import run_default_suite
from dams_simulator.visualize import plot_deadline_curves, plot_overall_success

results = run_default_suite()
plot_deadline_curves(results)
plot_overall_success(results)
```

## Project Layout

```
dams_simulator/
|-- models.py          # Shared dataclasses for frames and paths
|-- video_trace.py     # 30FPS IPB trace generator + helpers
|-- scheduler.py       # DAMS, RR, MinRTT, SRPT-ECF implementations
|-- simulation.py      # Discrete-event simulator & batch runner
|-- visualize.py       # Matplotlib plotting utilities
|-- results/           # (empty) drop-in folder for saved charts/data
\-- README.md
```

### Traffic generation
`video_trace.generate_video_trace()` builds a deterministic GOP-style IPB trace (default: 120 KB I-frames every 1 s, 60 KB P-frames every 5 frames, 20 KB B-frames otherwise). Each frame arrives at `frame_idx / fps` seconds with a 300 ms deadline slack so comparisons remain reproducible.

### Network model
`simulation.build_default_paths()` defines two configurable `PathConfig` entries (bandwidth + latency). The simulator keeps a `PathState` (available time, busy time, bytes sent) for each path and advances time to the next arrival or transmission completion event.

### Scheduling algorithms

* **DAMS** - prioritizes higher-priority frames, checks if the aggregate capacity until each frame's deadline is sufficient, and (greedily) splits large blocks across currently idle paths proportional to their near-term capacity.
* **RR** - round-robin over ready frames regardless of deadline; useful as a naive baseline.
* **MinRTT** - prefer the lowest-latency paths when selecting where to send a full frame.
* **SRPT-ECF** - greedily picks the block/path combination that minimizes completion time by looking at smallest remaining size (SRPT) and earliest finish (ECF).

All schedulers share a common interface, so extending the prototype (e.g., adding ECF variants or deadline-drop policies) only requires implementing `select_transmissions(...)`.

## Experiments and Plots

`simulation.run_default_suite()` runs every scheduler on the same trace/path settings and returns a dictionary of `SimulationResult` objects containing:

* Per-frame outcomes (arrival, deadline, finish time, drop flag, deadline success)
* Per-path utilization statistics
* A chronological transmission log
* Application-level metrics: average bitrate (only data arriving before deadlines) and total rebuffering time (missed frames / FPS)
* Transport-layer waste tracking: total bytes sent vs. bytes that missed their deadlines

`visualize.py` turns these results into:

1. **Deadline success curves** - sweep acceptable end-to-end delay thresholds and plot the fraction of frames each scheduler delivers fast enough.
2. **Bar charts** - compare overall success rates or split by I/P/B frame types to highlight how DAMS protects important frames.

Customize the scenarios by regenerating traces (longer durations, different GOP rhythms) or editing `PathConfig` to model Wi-Fi/5G extremes. Because the simulator is deterministic, you can finish a usable comparison (code + figures) in roughly a day.

## Next Steps

* Integrate a CLI (`python -m dams_simulator.cli --algo DAMS --duration 8 --wifi 8:25 --cell 3:70`) for quick parameter sweeps.
* Export CSV/JSON summaries for slides or downstream analysis.
* Swap in a more detailed network model (e.g., SimPy queues or Mahimahi traces) if you need to demo interactions with realistic RTT jitter.

Even without MPQUIC, this prototype captures the scheduling insights from the paper and makes it easy to explain how deadline-aware multipath decisions impact streaming quality.
