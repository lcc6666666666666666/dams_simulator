import os
import csv

# 你要转换的原始 log 列表（相对路径）
LOG_FILES = [
    "dams_simulator/traces/bus1.txt",
    "dams_simulator/traces/bus2.txt",
    "dams_simulator/traces/train1.txt",
    "dams_simulator/traces/train2.txt",
    "dams_simulator/traces/car1.txt",
    "dams_simulator/traces/car2.txt",
]

def convert_log_to_csv(input_path: str, output_path: str):
    """
    把 HSDPA 原始 log 转成 CSV:
    输入行格式：unix_ts ms_ts lat lon bytes_delta dt_ms
    输出列：time_s, bandwidth_Mbps
    """
    total_bits = 0.0
    total_time = 0.0
    t = 0.0  # 相对时间，从 0 秒开始累加

    samples = []  # 暂存 (dt_s, bw_Mbps)

    with open(input_path, "r") as fin:
        for line in fin:
            line = line.strip()
            if not line:
                continue
            parts = line.split()
            if len(parts) < 6:
                # 有些行可能不完整，直接跳过
                continue

            # 最后两列：bytes_delta 和 dt_ms
            try:
                bytes_delta = int(parts[4])
                dt_ms = int(parts[5])
            except ValueError:
                continue

            if dt_ms <= 0:
                continue

            dt_s = dt_ms / 1000.0
            # 当前区间平均带宽（Mb/s）
            bw_mbps = bytes_delta * 8 / dt_s / 1e6

            samples.append((dt_s, bw_mbps))

            total_bits += bytes_delta * 8
            total_time += dt_s

    if total_time == 0:
        print(f"[WARN] {input_path} 总时长为 0，跳过")
        return

    avg_mbps = total_bits / total_time / 1e6
    print(f"[INFO] {input_path} 平均带宽约为 {avg_mbps:.3f} Mbps, 时长 {total_time:.1f} s")

    # 写 CSV
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", newline="") as fout:
        writer = csv.writer(fout)
        writer.writerow(["time_s", "bandwidth_Mbps"])
        t = 0.0
        for dt_s, bw_mbps in samples:
            writer.writerow([f"{t:.3f}", f"{bw_mbps:.6f}"])
            t += dt_s

    print(f"[OK] 写出 CSV -> {output_path}\n")


def main():
    for path in LOG_FILES:
        if not os.path.exists(path):
            print(f"[WARN] 未找到文件: {path}，跳过")
            continue
        base, _ = os.path.splitext(path)
        out_csv = base + ".csv"  # bus1.csv, train1.csv ...
        convert_log_to_csv(path, out_csv)


if __name__ == "__main__":
    main()
