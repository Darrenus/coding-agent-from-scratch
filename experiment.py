"""对照实验：工具描述会不会改变模型的工具选择？"""
import json
import random
import statistics
import sys
from datetime import datetime

import agent

TASK = "在这个项目里，哪些地方用到了 requests 这个库？列出文件和行号。"

ARMS = {
    "vague": "搜索",
    "detailed": (
        "在工作目录下递归搜索正则，返回匹配的 文件:行号:内容。"
        "当你想知道某个符号、字符串或 import 出现在哪里时，"
        "优先用它，而不是把文件一个个读一遍——它便宜得多。"
    ),
}

GREP_SCHEMA = next(t for t in agent.TOOLS if t["function"]["name"] == "grep")


def one_trial(description):
    """跑一次，返回这次的几个指标。"""
    GREP_SCHEMA["function"]["description"] = description
    # outcome = agent.run_agent(TASK, verbose=False)
    outcome = agent.run_agent(TASK, verbose=False, approve=agent.always_approve)
    tools = outcome["tools"]
    return {
        "grep_first": bool(tools) and tools[0] == "grep",
        "steps": outcome["steps"],
        "prompt": outcome["prompt"],
        "completion": outcome["completion"],
        "tools": tools,
    }


def main(n):
    results = {name: [] for name in ARMS}

    for _ in range(n):
        # 交替跑，且每轮随机先后：把「时间」这个混杂变量摊平
        order = list(ARMS.items())
        random.shuffle(order)
        for name, description in order:
            results[name].append(one_trial(description))
            print(".", end="", flush=True)
    print("\n")

    # 先存原始数据，再算汇总。汇总是单行道，原始数据能被反复追问。
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = f"results-{stamp}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"原始数据已存: {path}\n")

    print(f"{'arm':<10} {'grep 先行':>12} {'平均步数':>10} {'平均输入':>12} {'标准差':>10}")
    for name, trials in results.items():
        hits = sum(t["grep_first"] for t in trials)
        steps = statistics.mean(t["steps"] for t in trials)
        prompts = [t["prompt"] for t in trials]
        spread = statistics.stdev(prompts) if len(prompts) > 1 else 0.0
        print(f"{name:<10} {hits:>6}/{len(trials):<5} {steps:>10.1f} "
              f"{statistics.mean(prompts):>12,.0f} {spread:>10,.0f}")

    print("\n按「第一步是否用了 grep」分组：")
    for name, trials in results.items():
        for used_grep in (True, False):
            subset = [t["prompt"] for t in trials if t["grep_first"] is used_grep]
            if subset:
                label = "grep 先行" if used_grep else "没用 grep"
                print(f"  {name:<10} {label:<10} n={len(subset):<3} "
                      f"平均输入 {statistics.mean(subset):>8,.0f}")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 10)