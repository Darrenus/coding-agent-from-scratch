"""区间读值不值：省了 token，代价是多走几步——答案还对不对？"""
import os
import statistics
import sys
import json
from datetime import datetime

os.environ.setdefault("AGENT_WORKSPACE",
                      "/Users/rong/Job Finding/reference/mini-swe-agent")
os.makedirs("results", exist_ok=True)

import agent
import context

TASKS = [
    ("swebench 批量运行时 docker 镜像名是怎么拼出来的？给出具体函数名。",
     ["get_swebench_docker_image_name"]),
    ("DefaultAgent 超过 step limit 之后会抛出什么异常？给出异常类名。",
     ["LimitsExceeded"]),
    ("LitellmModel 是怎么统计 API 花费的？给出相关函数名或全局对象名。",
     ["_calculate_cost", "GLOBAL_MODEL_STATS"]),
]

def run_one(task, expected, use_ranges):
    agent.RANGE_READS = use_ranges
    out = agent.run_agent(task, max_steps=10, verbose=False,
                          approve=agent.always_approve, repo_map=True)
    out["task"] = task
    out["correct"] = any(k in (out["answer"] or "") for k in expected)
    out["chars"] = sum(context.profile(out["messages"]).values())
    return out

def main(repeats):
    results = {True: [], False: []}
    for _ in range(repeats):
        for task, expected in TASKS:
            for use_ranges in (True, False):     # 交替，摊平时间这个混杂变量
                results[use_ranges].append(run_one(task, expected, use_ranges))
                print("." if use_ranges else "o", end="", flush=True)
    print("\n")

    path = f"results/context-{datetime.now():%Y%m%d-%H%M%S}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump({
            str(k): [{"task": r["task"], "correct": r["correct"],
                      "steps": r["steps"], "prompt": r["prompt"],
                      "cached": r["cached"], "completion": r["completion"],
                      "chars": r["chars"], "tools": r["tools"],
                      "answer": r["answer"]} for r in v]
            for k, v in results.items()}, f, ensure_ascii=False, indent=2)
    print(f"原始数据已存: {path}\n")

    print(f"{'区间读':<7}{'答对':>8}{'步数':>7}{'输入':>10}{'非缓存':>10}{'输出':>8}{'上下文字符':>12}")
    for use_ranges in (True, False):
        rows = results[use_ranges]
        print(f"{'开' if use_ranges else '关':<7}"
              f"{sum(r['correct'] for r in rows):>5}/{len(rows):<2}"
              f"{statistics.mean(r['steps'] for r in rows):>7.1f}"
              f"{statistics.mean(r['prompt'] for r in rows):>10,.0f}"
              f"{statistics.mean(r['prompt'] - r['cached'] for r in rows):>10,.0f}"
              f"{statistics.mean(r['completion'] for r in rows):>8,.0f}"
              f"{statistics.mean(r['chars'] for r in rows):>12,.0f}")

if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 3)