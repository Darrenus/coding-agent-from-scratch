"""模块 3 的问题：四级匹配策略里，后三级到底会不会被用到？

用法：
    python3 edit_experiment.py 3 cart.py          # 干净文件
    python3 edit_experiment.py 3 cart_dirty.py    # 带行尾空白
"""
import collections
import json
import os
import sys
from datetime import datetime
import agent

from editor import STRATEGY_NAMES as STRATEGIES

RESULTS_DIR = "results"

TASK_TEMPLATES = [
    "{f} 里的税率要从 5% 改成 8%。",
    "{f} 的 Cart.subtotal 方法请用 sum() 一行实现，行为保持不变。",
    "{f} 的 Cart.add 方法要拒绝价格为负的商品，抛 ValueError。",
    "{f} 的 Cart.discount 边界检查有问题，percent 等于 100 应该是合法的。",
]


def classify(result_line):
    """从 edit_file 的返回里认出命中的策略。"""
    if result_line.startswith("错误"):
        return "失败"
    if result_line.startswith("已创建"):
        return "新建文件"
    for name in STRATEGIES:
        if name in result_line:
            return name
    return "其他"


def main(repeats, target):
    with open(target, encoding="utf-8") as f:
        pristine = f.read()

    def restore():
        with open(target, "w", encoding="utf-8") as f:
            f.write(pristine)

    tasks = [t.format(f=target) for t in TASK_TEMPLATES]
    counter = collections.Counter()
    all_edits = []
    records = []

    for _ in range(repeats):
        for task in tasks:
            restore()
            outcome = agent.run_agent(
                task, verbose=False, approve=agent.always_approve, max_steps=8)
            edits = [t for t in outcome["trace"] if t["tool"] == "edit_file"]
            for edit in edits:
                kind = classify(edit["result"])
                counter[kind] += 1
                all_edits.append({"task": task, "kind": kind, **edit})
            records.append({
                "task": task,
                "steps": outcome["steps"],
                "prompt": outcome["prompt"],
                "edits": [classify(e["result"]) for e in edits],
            })
            print(".", end="", flush=True)
    restore()
    print("\n")

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    os.makedirs(RESULTS_DIR, exist_ok=True)
    path = os.path.join(RESULTS_DIR, f"edits-{target.replace('.py', '')}-{stamp}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"target": target, "records": records, "edits": all_edits},
                  f, ensure_ascii=False, indent=2)
    print(f"原始数据已存: {path}\n")

    total = sum(counter.values()) or 1
    print(f"{'策略':<14} {'次数':>6} {'占比':>8}")
    for kind, n in counter.most_common():
        print(f"{kind:<14} {n:>6} {n / total:>7.0%}")
    print(f"{'合计':<14} {sum(counter.values()):>6}")

    print(f"\n共记录 {len(all_edits)} 次编辑的完整参数")

if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 3,
         sys.argv[2] if len(sys.argv) > 2 else "cart.py")