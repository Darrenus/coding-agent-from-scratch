"""repo map 到底省不省 token？

在 mini-swe-agent（112 个文件）上跑只读问答任务，对比开/关地图。
"""
import os
import statistics
import sys

os.environ.setdefault("AGENT_WORKSPACE", "/Users/rong/Job Finding/reference/mini-swe-agent")

import agent

TASKS = [
    "DefaultAgent 超过 step limit 之后会抛什么异常？指出定义在哪个文件。",
    "swebench 批量运行时 docker 镜像名是怎么拼出来的？指出具体函数。",
    "LitellmModel 是怎么统计 API 花费的？指出具体代码位置。",
]


def main(repeats):
    results = {True: [], False: []}
    map_chars = []
    for _ in range(repeats):
        for task in TASKS:
            map_chars.append(len(agent.build_repo_map(task)))
            for use_map in (True, False):
                out = agent.run_agent(task, max_steps=8, verbose=False,
                                      approve=agent.always_approve, repo_map=use_map)
                results[use_map].append(out)
                print("." if use_map else "o", end="", flush=True)
    print("\n")
    print(f"地图平均长度 {statistics.mean(map_chars):.0f} 字符\n")

    header = f"{'地图':<6}{'步数':>7}{'输入':>10}{'其中缓存':>10}{'非缓存输入':>12}{'输出':>8}{'读文件':>8}"
    print(header)
    for use_map in (True, False):
        rows = results[use_map]
        prompt = statistics.mean(r["prompt"] for r in rows)
        cached = statistics.mean(r["cached"] for r in rows)
        print(f"{'开' if use_map else '关':<6}"
              f"{statistics.mean(r['steps'] for r in rows):>7.1f}"
              f"{prompt:>10,.0f}"
              f"{cached:>10,.0f}"
              f"{prompt - cached:>12,.0f}"
              f"{statistics.mean(r['completion'] for r in rows):>8,.0f}"
              f"{statistics.mean(r['tools'].count('read_file') for r in rows):>8.1f}")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 2)