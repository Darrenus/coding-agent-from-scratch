import os
import requests
import json
import re

API_URL = "https://api.deepseek.com/v1/chat/completions"
MODEL = "deepseek-v4-flash"
KEY_PATH = os.path.expanduser("~/.config/agent-from-scratch/env")
MAX_HITS = 200

WORKSPACE = os.path.realpath(".")
DENY_PATTERNS = (".env", ".git/", "id_rsa", ".pem", ".key", "credential", ".netrc")

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

def load_key():
    """从工作目录之外读 API Key。"""
    with open(KEY_PATH) as f:
        for line in f:
            if line.startswith("DEEPSEEK_API_KEY="):
                return line.split("=",1)[1].strip()
    raise RuntimeError(f"在 {KEY_PATH} 里没找到 DEEPSEEK_API_KEY")

API_KEY = load_key()

def call_model(messages, tools=None):
    """发一次请求，返回模型这一轮的 message 字典。"""
    payload = {"model":MODEL, "messages":messages}
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
    return data["choices"][0]["message"], data.get("usage",{})

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读取一个文件的完整内容，返回带行号的文本。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "相对于当前工作目录的文件路径"
                    }
                },
                "required": ["path"],
            },
        },
    },

    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "列出文件",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "目录路径"}
                },
                "required": ["path"],
            },
        },
    },

        {
        "type": "function",
        "function": {
            "name": "grep",
            "description": (
                "在工作目录下递归搜索正则，返回匹配的 文件:行号:内容。"
                "当你想知道某个符号、字符串或 import 出现在哪里时，"
                "优先用它，而不是把文件一个个读一遍——它便宜得多。"
            
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Python 正则表达式"},
                    "path": {"type": "string", "description": "搜索起点目录，默认当前目录"},
                },
                "required": ["pattern"],
            },
        },
    },
]

def read_file(path):
    """读文件，返回带行号的内容。"""
    with open(safe_path(path), encoding="utf-8") as f:
        lines = f.read().splitlines()
    return "\n".join(f"{i:6d}\t{line}" for i, line in enumerate(lines, 1))

def list_files(path="."):
    """列出目录内容，目录名后面加/。"""
    target = safe_path(path)
    names = sorted(os.listdir(target))
    return "\n".join(
        name + "/" if os.path.isdir(os.path.join(target, name)) else name
        for name in names
    )

TOOL_REGISTRY = {
    "read_file": read_file,
    "list_files": list_files,
    "grep": grep
}

# schema 给模型看，registry 给 Python 跑，是两份手写的东西。它们一旦不同步，模型会调到一个不存在的工具，而且不会报错——只会静默变笨。
_schema_names = {t["function"]["name"] for t in TOOLS}
assert _schema_names == set(TOOL_REGISTRY), (
    f"TOOLS 与 TOOL_REGISTRY 不一致 | "
    f"只在 schema 里: {_schema_names - set(TOOL_REGISTRY)} | "
    f"只在实现里: {set(TOOL_REGISTRY) - _schema_names}"
)

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

SYSTEM_PROMPT = """你是一个命令行 coding agent，工作目录就是当前目录。一步一步来，每一步都要可验证。你在无人值守地运行：不要向用户提问，不要等待确认，自己做判断并把任务做完。任务完成后，用一句话说明你做了什么、怎么确认它是对的。"""

def run_agent(task, max_steps=10, verbose=True):
    """
    从
    跑一个任务直到模型不再需要工具。
    到
    跑一个任务。返回包含答案、用量和工具调用序列的字典。
    """

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": task},
    ]
    totals = {"steps": 0, "prompt": 0, "completion": 0, "cached": 0}
    tools_used = []

    def result(answer, exit_reason):
        return {
            "answer": answer,
            "exit_reason": exit_reason,
            "tools": tools_used,
            **totals,
        }

    for step in range(1, max_steps + 1):
        message, usage = call_model(messages, TOOLS)
        messages.append(message)

        totals["steps"] = step
        totals["prompt"] += usage.get("prompt_tokens", 0)
        totals["completion"] += usage.get("completion_tokens", 0)
        totals["cached"] += usage.get("prompt_cache_hit_tokens", 0)

        if verbose and message.get("content"):
            print(f"[{step}] {message['content']}")

        calls = message.get("tool_calls")
        if not calls:
            return result(message.get("content", ""), "finished")

        for call in calls:
            name = call["function"]["name"]
            tools_used.append(name)
            if verbose:
                print(f"[{step}] → {name}({call['function']['arguments']})")

            tool_result = run_tool(name, call["function"]["arguments"])
            if verbose and tool_result.startswith("错误"):
                print(f"[{step}]   ⚠ {tool_result.splitlines()[0][:100]}")

            messages.append({
                "role": "tool",
                "tool_call_id": call["id"],
                "content": tool_result,
            })

    return result("", "step_limit")

if __name__ == "__main__":
    # 模块 1: 把两条 curl 变成一个循环
    '''
    # 1.1
    message = call_model([{"role":"user","content":"用一句话说明什么是递归"}])
    print(message["content"])
    '''

    '''
    # 1.2
    messages = [
        {"role":"user","content":"读一下 calc.py，告诉我里面有什么问题"}]
    message = call_model(messages, tools=TOOLS)
    print("=== 模型的回复 ===")
    print(json.dumps(message, ensure_ascii=False, indent=2))
    call = message["tool_calls"][0]
    result = run_tool(call["function"]["name"], call["function"]["arguments"])
    print("=== 工具执行结果 ===")
    print(result)
    '''

    '''
    # 1.3
    run_agent("读一下 calc.py，告诉我 add 函数有什么问题")
    '''

    # 模块 2：工具描述是prompt，不是注释
    '''
    # 2.1
    run_agent("这个项目里哪个文件定义了add函数？它有什么问题？")
    '''

    # 2.4
    outcome = run_agent("在这个项目里，哪些地方用到了 requests 这个库？列出文件和行号。")
    print(f"\n--- {outcome['steps']} 步 · 输入 {outcome['prompt']:,} "
          f"(缓存 {outcome['cached']:,}) · 输出 {outcome['completion']:,} "
          f"· 工具 {outcome['tools']} ---")
