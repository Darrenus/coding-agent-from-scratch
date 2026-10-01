"""统一评测：一个任务集、一个跑批器、一个带噪声校准的比较器。

此前每个实验各写各的指标和格式，结论之间无法比较；而且只有一次意外的 A/A
测过底噪，其余结论都是在不知道噪声多大的情况下下的。

所以这里把 A/A 做成默认行为：每次消融自动复制一份基线作为对照组，
「效应是否超过噪声」成为默认输出而不是事后补做。
"""
import json
import os
import random
import shutil
import statistics
import time
from datetime import datetime
from typing import NamedTuple

import agent
import context

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
RESULTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")


class Task(NamedTuple):
    id: str
    prompt: str
    expect: tuple          # 答案里必须出现的关键词之一
    fixture: str = ""      # fixtures/ 下的子目录名；空表示只读任务
    verify: str = ""       # 跑完后用来独立复验的命令，空表示不复验


TASKS = [
    Task("docker-image",
         "swebench 批量运行时 docker 镜像名是怎么拼出来的？给出具体函数名。",
         ("get_swebench_docker_image_name",)),
    Task("step-limit",
         "DefaultAgent 超过 step limit 之后会抛出什么异常？给出异常类名。",
         ("LimitsExceeded",)),
    Task("cost-tracking",
         "LitellmModel 是怎么统计 API 花费的？给出相关函数名或全局对象名。",
         ("_calculate_cost", "GLOBAL_MODEL_STATS")),
]

REPAIR_TASKS = [
    Task("fix-shop",
         "test_shop.py 有多条测试失败。逐个修复 shop.py 里的 bug，每修一个就跑 "
         "python3 -m unittest test_shop 确认进展，直到全部通过。不要修改测试文件。",
         ("", ),                       # 正确性靠 verify 判定，不看文本
         fixture="shop",
         verify="python3 -m unittest test_shop"),
]


def _apply(config):
    """把配置写进 agent 的全局开关，返回旧值用于还原。

    agent 的开关是模块级全局——这是模块 2 就埋下的坏味道。
    这里至少保证 arm 之间不互相污染：每次用完立刻还原。
    """
    old = {k: getattr(agent, k) for k in config}
    for k, v in config.items():
        setattr(agent, k, v)
    return old


def _prepare_workspace(task, root):
    """只读任务直接用 root；修复任务复制一份干净夹具。"""
    if not task.fixture:
        return root
    work = os.path.join(RESULTS, "_work", task.fixture)
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(os.path.dirname(work), exist_ok=True)
    shutil.copytree(os.path.join(FIXTURES, task.fixture), work)
    return work


def _verify(task, workspace):
    """用独立的命令复验，而不是信 agent 自己说「我跑通了」。"""
    if not task.verify:
        return None
    import subprocess
    proc = subprocess.run(task.verify, shell=True, cwd=workspace,
                          capture_output=True, text=True, timeout=120)
    return proc.returncode == 0


def run_once(task, config, root, max_steps):
    old = _apply(config)
    workspace = _prepare_workspace(task, root)
    agent.WORKSPACE = os.path.realpath(workspace)
    started = time.time()
    try:
        out = agent.run_agent(task.prompt, max_steps=max_steps, verbose=False,
                              approve=agent.always_approve, repo_map=True)
    finally:
        _apply(old)

    verified = _verify(task, workspace)
    answer = out["answer"] or ""
    correct = verified if verified is not None else any(k and k in answer for k in task.expect)

    return {
        "task": task.id, "correct": bool(correct),
        "steps": out["steps"], "prompt": out["prompt"], "cached": out["cached"],
        "uncached": out["prompt"] - out["cached"], "completion": out["completion"],
        "chars": sum(context.profile(out["messages"]).values()),
        "seconds": round(time.time() - started, 1),
        "tools": out["tools"], "exit_reason": out["exit_reason"], "answer": answer,
    }


METRICS = ("steps", "prompt", "uncached", "completion", "chars", "seconds")


def run_suite(arms, tasks, repeats=3, max_steps=15, root=".", tag="eval"):
    """arms: {名字: 配置 dict}。基线（第一个）会被自动复制成 A/A 对照组。"""
    names = list(arms)
    baseline = names[0]
    arms = dict(arms)
    arms[f"{baseline}#AA"] = dict(arms[baseline])      # ← A/A 自动加入

    rows = []
    for _ in range(repeats):
        for task in tasks:
            order = list(arms.items())
            random.shuffle(order)                      # 消掉顺序效应
            for name, config in order:
                row = run_once(task, config, root, max_steps)
                row["arm"] = name
                rows.append(row)
                print(".", end="", flush=True)
    print("\n")

    os.makedirs(RESULTS, exist_ok=True)
    path = os.path.join(RESULTS, f"{tag}-{datetime.now():%Y%m%d-%H%M%S}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)
    print(f"原始数据: {path}\n")

    report(rows, baseline)
    return rows


def _agg(rows, arm):
    sel = [r for r in rows if r["arm"] == arm]
    out = {m: statistics.mean(r[m] for r in sel) for m in METRICS}
    out["correct"] = sum(r["correct"] for r in sel)
    out["n"] = len(sel)
    return out


def report(rows, baseline):
    arms = sorted({r["arm"] for r in rows}, key=lambda a: (a.endswith("#AA"), a))
    agg = {a: _agg(rows, a) for a in arms}
    aa = f"{baseline}#AA"

    print(f"{'arm':<18}{'答对':>8}" + "".join(f"{m:>11}" for m in METRICS))
    for a in arms:
        g = agg[a]
        print(f"{a:<18}{g['correct']:>4}/{g['n']:<3}"
              + "".join(f"{g[m]:>11,.1f}" for m in METRICS))

    if aa not in agg:
        return

    others = [a for a in arms if a not in (baseline, aa)]
    print(f"\n基线取 {baseline} 与 {aa} 的合并估计（同配置，分开用等于浪费一半数据）；")
    print(f"底噪 = 两者之差。")
    print(f"\n{'指标':<12}{'基线':>12}{'底噪':>8}" + "".join(f"{a:>14}" for a in others))

    for m in METRICS:
        base = (agg[baseline][m] + agg[aa][m]) / 2 or 1
        floor = abs(agg[aa][m] - agg[baseline][m]) / base
        cells = []
        for a in others:
            effect = abs(agg[a][m] - base) / base
            cells.append(f"{effect:>11.0%} {'✓' if effect > floor * 1.5 else '✗'}")
        print(f"{m:<12}{base:>12,.0f}{floor:>7.0%} " + "".join(f"{c:>14}" for c in cells))

    # 正确率单独算：它是比率不是均值，底噪要用两条 A/A 的通过率之差
    def rate(a):
        return agg[a]["correct"] / agg[a]["n"]
    base_rate = (agg[baseline]["correct"] + agg[aa]["correct"]) / (agg[baseline]["n"] + agg[aa]["n"])
    floor_rate = abs(rate(aa) - rate(baseline))
    cells = []
    for a in others:
        effect = abs(rate(a) - base_rate)
        cells.append(f"{effect:>11.0%} {'✓' if effect > floor_rate * 1.5 else '✗'}")
    print(f"{'正确率':<12}{base_rate:>11.0%}{floor_rate:>7.0%} " + "".join(f"{c:>14}" for c in cells))

    print("\n✓ = 效应超过底噪 1.5 倍；✗ = 与噪声无法区分")