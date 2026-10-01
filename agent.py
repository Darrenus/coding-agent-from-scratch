"""从零手搓的 coding agent"""
import json
import os
import re
import requests
import sandbox
import subprocess
import editor
import sys

API_URL = "https://api.deepseek.com/v1/chat/completions"
MODEL = "deepseek-v4-flash"
KEY_PATH = os.path.expanduser("~/.config/agent-from-scratch/env")

MAIN_TOOLS = None      # None = 全部工具；设成集合可限制主 agent（用于消融）
MAX_HITS = 200
MAX_READ_LINES = 400
MAIN_MAX_READ_LINES = None
RANGE_READS = True
BASH_TIMEOUT = 60
MAX_BASH_OUTPUT = 8000
REQUIRE_SANDBOX = True       # 沙箱不可用时拒绝执行，而不是降级放行
READ_ONLY_TOOLS = {"read_file", "list_files", "grep"}
SUB_AGENT_STEPS = 8
MAX_DELEGATE_DEPTH = 1
HIDDEN_TESTS = None      # 工作区之外的测试目录；None 表示没有隐藏测试

_depth = 0
_sub_usage = {"calls": 0, "steps": 0, "prompt": 0, "cached": 0, "completion": 0}

WORKSPACE = os.path.realpath(os.environ.get("AGENT_WORKSPACE", "."))
assert os.path.isabs(WORKSPACE) and WORKSPACE != os.sep, f"WORKSPACE 不合法: {WORKSPACE!r}"

DENY_PATTERNS = (".env", ".git/", "id_rsa", ".pem", ".key", "credential", ".netrc")

def _read_cap():
    """主 agent 和子 agent 可以有不同的读取上限。

    用来把「模型会不会主动委派」和「委派有没有用」这两个问题分开：
    勒紧主 agent 的读取能力，它就不得不派出去。
    """
    if _depth == 0 and MAIN_MAX_READ_LINES is not None:
        return MAIN_MAX_READ_LINES
    return MAX_READ_LINES

def safe_path(path):
    """解析路径，拒绝越界和敏感文件。返回绝对路径。"""
    target = os.path.realpath(os.path.join(WORKSPACE, path))

    if target != WORKSPACE and not target.startswith(WORKSPACE + os.sep):
        raise ValueError(f"拒绝：{path} 在工作目录之外")

    relative = os.path.relpath(target, WORKSPACE).lower()
    for pattern in DENY_PATTERNS:
        if pattern in relative:
            raise ValueError(f"拒绝：{relative} 看起来像敏感文件")

    return target


# ── 工具注册 ──────────────────────────────────────────────
# schema 和实现在同一处声明，定义函数就是注册它，不可能只写一半。

TOOLS = []
TOOL_REGISTRY = {}


def tool(name, description, properties, required):
    """把函数注册为工具。"""
    def decorator(fn):
        if name in TOOL_REGISTRY:
            raise ValueError(f"工具名重复：{name}")
        TOOLS.append({
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                },
            },
        })
        TOOL_REGISTRY[name] = fn
        return fn
    return decorator


@tool(
    name="read_file",
    description=(
        "读取文件内容，返回带行号的文本。"
        "可以只读某个行区间（start/end，1-based，含两端）。"
        "大文件请优先用区间——整文件读取会占满上下文，而且每一轮都要重发。"
        "grep 的结果里带行号，可以直接拿来定位区间。"
    ),
    properties={
        "path": {"type": "string", "description": "文件路径"},
        "start": {"type": "integer", "description": "起始行号（1-based），省略表示从第一行"},
        "end": {"type": "integer", "description": "结束行号（含），省略表示到最后一行"},
    },
    required=["path"],
)
def read_file(path, start=None, end=None):
    """读文件，返回带行号的内容。可选行区间。"""
    if not RANGE_READS:
        start = end = None
    with open(safe_path(path), encoding="utf-8") as f:
        lines = f.read().splitlines()

    total = len(lines)
    if total == 0:
        return f"（{path} 是空文件）"

    first = max(1, start or 1)
    if first > total:
        return f"错误：{path} 共 {total} 行，start={start} 超出范围。"
    last = min(total, end or total)

    cap = _read_cap()
    note = ""
    if last - first + 1 > cap:
        last = first + cap - 1
        note = (f"\n（已截断到 {cap} 行。文件共 {total} 行，"
                f"用 start={last + 1} 继续读。）")

    body = "\n".join(f"{i:6d}\t{lines[i - 1]}" for i in range(first, last + 1))
    header = ""
    if first > 1 or last < total:
        header = f"（{path} 第 {first}–{last} 行，共 {total} 行）\n"
    return header + body + note


@tool(
    name="list_files",
    description="列出目录里的文件和子目录，目录名后带 /。",
    properties={"path": {"type": "string", "description": "目录路径，默认当前目录"}},
    required=[],
)
def list_files(path="."):
    """列出目录内容，目录名后面加 /。"""
    target = safe_path(path)
    names = sorted(os.listdir(target))
    return "\n".join(
        name + "/" if os.path.isdir(os.path.join(target, name)) else name
        for name in names
    )


@tool(
    name="grep",
    description=(
        "在工作目录下递归搜索正则，返回匹配的 文件:行号:内容。"
        "当你想知道某个符号、字符串或 import 出现在哪里时，"
        "优先用它，而不是把文件一个个读一遍——它便宜得多。"
    ),
    properties={
        "pattern": {"type": "string", "description": "Python 正则表达式"},
        "path": {"type": "string", "description": "搜索起点目录，默认当前目录"},
    },
    required=["pattern"],
)
def grep(pattern, path="."):
    """在目录下递归搜索正则，返回 文件:行号:内容。"""
    root = safe_path(path)
    regex = re.compile(pattern)
    hits = []

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for name in filenames:
            if name.startswith("."):
                continue
            full = os.path.join(dirpath, name)
            try:
                with open(full, encoding="utf-8") as f:
                    for i, line in enumerate(f, 1):
                        if regex.search(line):
                            rel = os.path.relpath(full, WORKSPACE)
                            hits.append(f"{rel}:{i}:{line.rstrip()}")
                            if len(hits) >= MAX_HITS:
                                return "\n".join(hits) + f"\n(已截断，超过 {MAX_HITS} 条)"
            except (UnicodeDecodeError, OSError):
                continue

    return "\n".join(hits) if hits else "(无匹配)"


@tool(
    name="edit_file",
    description=(
        "编辑文件：把 old_str 替换成 new_str。"
        "old_str 应与文件内容一致（允许空白和缩进的细微差异），"
        "且必须能唯一定位——请带上足够的上下文。"
        "old_str 传空字符串表示新建文件。"
    ),
    properties={
        "path": {"type": "string", "description": "文件路径"},
        "old_str": {"type": "string", "description": "要被替换的原文"},
        "new_str": {"type": "string", "description": "替换后的新内容"},
    },
    required=["path", "old_str", "new_str"],
)
def edit_file(path, old_str, new_str):
    """SEARCH/REPLACE 编辑。匹配策略见 editor.py。"""
    target = safe_path(path)

    if old_str == "":
        if os.path.exists(target):
            return f"错误：{path} 已存在，不能用空 old_str 创建。"
        with open(target, "w", encoding="utf-8") as f:
            f.write(new_str)
        return f"已创建 {path}（{len(new_str.splitlines())} 行）"

    with open(target, encoding="utf-8") as f:
        content = f.read()

    new_content, note = editor.apply_edit(content, old_str, new_str)
    if new_content is None:
        return f"错误：{note}"

    with open(target, "w", encoding="utf-8") as f:
        f.write(new_content)

    return f"已修改 {path}：{note}"

@tool(
    name="bash",
    description=(
        "在工作目录下执行 shell 命令，返回输出和退出码。"
        "命令运行在沙箱里：只能写工作目录，不能联网。"
        "改完代码一定要用它跑测试验证，不要只凭阅读就下结论。"
    ),
    properties={"command": {"type": "string", "description": "要执行的 shell 命令"}},
    required=["command"],
)
def bash(command):
    """在沙箱里执行命令。沙箱不可用时拒绝执行。"""
    argv = sandbox.wrap(command, WORKSPACE,
        deny_read=[HIDDEN_TESTS] if HIDDEN_TESTS else [])
    if argv is None:
        if REQUIRE_SANDBOX:
            return ("错误：当前平台没有可用的沙箱，已拒绝执行命令。"
                    "（可以把 REQUIRE_SANDBOX 设为 False 放行，但命令将不受任何约束。）")
        argv = ["/bin/bash", "-c", command]

    try:
        proc = subprocess.run(
            argv, cwd=WORKSPACE, capture_output=True, text=True,
            timeout=BASH_TIMEOUT, stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        return f"错误：命令超过 {BASH_TIMEOUT} 秒仍未结束，已终止。"

    parts = []
    if proc.stdout:
        parts.append(proc.stdout)
    if proc.stderr:
        parts.append("[stderr]\n" + proc.stderr)
    out = "\n".join(parts).strip() or "(无输出)"

    if len(out) > MAX_BASH_OUTPUT:
        half = MAX_BASH_OUTPUT // 2
        out = (out[:half]
               + f"\n…（输出过长，中间省略 {len(out) - MAX_BASH_OUTPUT} 字符）…\n"
               + out[-half:])

    return f"{out}\n[退出码 {proc.returncode}]"

@tool(
    name="delegate",
    description=(
        "派一个只读子 agent 去调查问题，返回它的结论。"
        "关键区别：子 agent 读过的文件内容不会进入你的上下文；"
        "而你自己读的每一个文件，全文都会留在对话历史里，并在之后的每一轮重新发送。"
        "所以凡是「要翻好几个文件才能回答」的子问题，派出去比自己逐个读便宜得多。"
        "问题要具体、自包含：子 agent 看不到你的对话。"
    ),
    properties={"question": {"type": "string", "description": "交给子 agent 的完整问题"}},
    required=["question"],
)
def delegate(question):
    """只读子 agent。轨迹用完即弃，只有结论回到主上下文。"""
    global _depth
    if _depth >= MAX_DELEGATE_DEPTH:
        return "错误：子 agent 不能再派子 agent，请自己完成这次调查。"

    _depth += 1
    try:
        out = run_agent(question, max_steps=SUB_AGENT_STEPS, verbose=False,
                        approve=always_approve, repo_map=True,
                        allowed_tools=READ_ONLY_TOOLS)
    finally:
        _depth -= 1

    _sub_usage["calls"] += 1
    for key in ("steps", "prompt", "cached", "completion"):
        _sub_usage[key] += out[key]

    answer = out["answer"] or "(子 agent 没有给出结论)"
    return f"{answer}\n\n[子 agent 用了 {out['steps']} 步、{out['prompt']:,} 输入 token]"

@tool(
    name="run_tests",
    description=(
        "运行隐藏的测试套件，返回每条测试通过还是失败、以及失败时的断言信息。"
        "你看不到测试源码——只能从失败信息反推代码应该满足什么行为。"
    ),
    properties={},
    required=[],
)
def run_tests():
    """在沙箱外运行工作区之外的测试，只回传结果摘要，不回传源码。"""
    if not HIDDEN_TESTS:
        return "错误：当前没有配置隐藏测试套件。"

    env = dict(os.environ, PYTHONPATH=WORKSPACE)
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "unittest", "discover",
             "-s", HIDDEN_TESTS, "-t", HIDDEN_TESTS, "-v"],
            cwd=WORKSPACE, env=env, capture_output=True, text=True,
            timeout=120, stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        return "错误：测试超过 120 秒未结束。"

    keep_prefix = ("AssertionError", "FAIL:", "ERROR:", "Ran ", "OK", "FAILED")
    kept = [
        line for line in (proc.stdout + proc.stderr).splitlines()
        if re.match(r"^test_\w+ .*\.\.\. ", line) or line.startswith(keep_prefix)
    ]
    return "\n".join(kept) or "(没有输出)"

def run_tool(name, arguments_json):
    """执行一次工具调用。永远返回字符串，永远不抛异常。"""
    if name not in TOOL_REGISTRY:
        return f"错误：没有名为 {name} 的工具。"
    try:
        args = json.loads(arguments_json)
    except json.JSONDecodeError as exc:
        return f"错误：工具 {name} 的参数不是合法的 JSON：{exc}。请重新发起这次调用。"
    try:
        return TOOL_REGISTRY[name](**args)
    except Exception as exc:
        return f"错误：{type(exc).__name__}: {exc}"


# ── 模型调用 ──────────────────────────────────────────────

def load_key():
    """从工作目录之外读 API key。"""
    with open(KEY_PATH) as f:
        for line in f:
            if line.startswith("DEEPSEEK_API_KEY="):
                return line.split("=", 1)[1].strip()
    raise RuntimeError(f"在 {KEY_PATH} 里没找到 DEEPSEEK_API_KEY")


API_KEY = load_key()


def call_model(messages, tools=None):
    """发一次请求，返回 (message, usage)。"""
    payload = {"model": MODEL, "messages": messages}
    if tools:
        payload["tools"] = tools

    response = requests.post(
        API_URL,
        headers={"Authorization": f"Bearer {API_KEY}"},
        json=payload,
        timeout=120,
    )
    response.raise_for_status()
    data = response.json()
    return data["choices"][0]["message"], data.get("usage", {})


# ── agent 循环 ────────────────────────────────────────────

SYSTEM_PROMPT = """你是一个命令行 coding agent，工作目录就是当前目录。

一步一步来，每一步都要可验证。

需要翻阅多个文件才能回答的子问题，优先用 delegate 派给子 agent，而不是自己逐个读——
你读过的文件会一直占据上下文，子 agent 的不会。

你在无人值守地运行：不要向用户提问，不要等待确认，自己做判断并把任务做完。

任务完成后，用一句话说明你做了什么、怎么确认它是对的。"""

WRITE_TOOLS = {"edit_file","bash"}

def build_repo_map(task, max_files=12):
    """为这个任务生成一张仓库地图。失败时返回空串，不影响主流程。"""
    try:
        import repomap
        tags = repomap.scan_repo(WORKSPACE)
        if not tags:
            return ""
        edges, definers = repomap.build_graph(tags, root=WORKSPACE)
        weights = repomap.edge_weights(edges, definers)
        seed = repomap.task_personalization(task, tags, WORKSPACE)
        rank = repomap.pagerank(list(tags), weights, personalization=seed)
        return repomap.render_map(tags, rank, max_files)
    except Exception as exc:
        return f"（仓库地图生成失败：{type(exc).__name__}: {exc}）"

def interactive_approve(name, arguments_json):
    """写操作需人工确认；只读工具直接放行，避免审批疲劳。

    从 /dev/tty 读而不是 stdin——stdin 经常被重定向（heredoc、管道、文件），
    那时 input() 会直接拿到 EOF。拿不到终端就一律拒绝。
    """
    if name not in WRITE_TOOLS:
        return True
    print(f"\n  ⚠ 准备执行 {name}")
    print(f"    {arguments_json[:500]}")
    try:
        with open("/dev/tty") as tty:
            print("    允许吗？[y/N] ", end="", flush=True)
            answer = tty.readline()
    except OSError:
        print("    拿不到终端，无法确认 → 拒绝。")
        return False
    return answer.strip().lower() in ("y", "yes")

def always_approve(name, arguments_json):
    """批量实验用：不问，直接放行。"""
    return True


def run_agent(task, max_steps=10, verbose=True, approve=None, repo_map=False, allowed_tools=None):
    """跑一个任务。返回答案、用量，以及完整的调用轨迹。"""
    approve = approve or interactive_approve
    if allowed_tools is None:
        allowed_tools = MAIN_TOOLS
    tools = TOOLS if allowed_tools is None else [
        t for t in TOOLS if t["function"]["name"] in allowed_tools]
    user_content = task
    if repo_map:
        mapping = build_repo_map(task)
        if mapping:
            user_content = f"{mapping}\n\n（以上是自动生成的参考，不保证完整。）\n\n任务：{task}"
    if _depth == 0:
        for key in _sub_usage:
            _sub_usage[key] = 0
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]
    totals = {"steps": 0, "prompt": 0, "completion": 0, "cached": 0}
    trace = []
    per_call = []

    def result(answer, exit_reason):
        return {
            "answer": answer,
            "exit_reason": exit_reason,
            "tools": [t["tool"] for t in trace],
            "trace": trace,
            "messages": messages,
            "per_call": per_call,
            "sub_calls": _sub_usage["calls"],
            "sub_steps": _sub_usage["steps"],
            "sub_prompt": _sub_usage["prompt"],
            "sub_completion": _sub_usage["completion"],
            "sub_cached": _sub_usage["cached"],
            **totals,
        }

    for step in range(1, max_steps + 1):
        message, usage = call_model(messages, tools)
        messages.append(message)

        totals["steps"] = step
        totals["prompt"] += usage.get("prompt_tokens", 0)
        totals["completion"] += usage.get("completion_tokens", 0)
        totals["cached"] += usage.get("prompt_cache_hit_tokens", 0)

        per_call.append({
            "step": step,
            "prompt": usage.get("prompt_tokens", 0),
            "cached": usage.get("prompt_cache_hit_tokens", 0),
            "completion": usage.get("completion_tokens", 0),
        })

        if verbose and message.get("content"):
            print(f"[{step}] {message['content']}")

        calls = message.get("tool_calls")
        if not calls:
            return result(message.get("content", ""), "finished")

        for call in calls:
            name = call["function"]["name"]
            arguments = call["function"]["arguments"]
            if verbose:
                print(f"[{step}] → {name}({arguments[:160]})")

            if allowed_tools is not None and name not in allowed_tools:
                tool_result = f"错误：子 agent 没有调用 {name} 的权限。"
            elif not approve(name, arguments):
                tool_result = "用户拒绝了这次操作。"
            else:
                tool_result = run_tool(name, arguments)

            if verbose and tool_result.startswith("错误"):
                print(f"[{step}]   ⚠ {tool_result.splitlines()[0][:100]}")

            trace.append({
                "step": step,
                "tool": name,
                "args": arguments,
                "result": tool_result.splitlines()[0][:200] if tool_result else "",
            })

            messages.append({
                "role": "tool",
                "tool_call_id": call["id"],
                "content": tool_result,
            })

    return result("", "step_limit")

if __name__ == "__main__":
    outcome = run_agent("calc.py 里的 add 函数写错了，它做的是减法。修好它，然后验证。")
    print(f"\n--- {outcome['steps']} 步 · 输入 {outcome['prompt']:,} "
          f"· 输出 {outcome['completion']:,} · 工具 {outcome['tools']} ---")