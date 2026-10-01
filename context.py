"""上下文预算：先搞清楚体积花在哪，再决定怎么省。"""
import json


def _size(obj) -> int:
    """消息的粗略体积：JSON 序列化后的字符数。

    不是精确 token 数，但做决策看的是比例，而且不引入额外依赖。
    真实 token 数可以用 API 返回的 prompt_tokens 反推出字符/token 比例来校准。
    """
    return len(json.dumps(obj, ensure_ascii=False))


def profile(messages):
    """把消息列表按来源分桶，返回 {桶名: 字符数}。

    tool 结果按**产生它的工具名**分桶——这才能回答「是哪个工具在撑爆上下文」。
    """
    call_names = {}          # tool_call_id -> 工具名
    buckets = {}

    def add(key, n):
        buckets[key] = buckets.get(key, 0) + n

    for msg in messages:
        role = msg["role"]
        content = msg.get("content")

        if role == "system":
            add("system", _size(content))
        elif role == "user":
            add("user/任务+地图", _size(content))
        elif role == "assistant":
            if content:
                add("assistant/文字", _size(content))
            for call in msg.get("tool_calls") or []:
                call_names[call["id"]] = call["function"]["name"]
                add("assistant/工具调用", _size(call))
        elif role == "tool":
            add(f"tool结果/{call_names.get(msg.get('tool_call_id'), '?')}", _size(content))

    return buckets


def report(messages, prompt_tokens=None):
    """打印体积构成。给了 prompt_tokens 就顺便校准字符/token 比例。"""
    buckets = profile(messages)
    total = sum(buckets.values()) or 1

    lines = [f"{'来源':<28}{'字符':>10}{'占比':>8}"]
    for key, size in sorted(buckets.items(), key=lambda kv: -kv[1]):
        lines.append(f"{key:<28}{size:>10,}{size / total:>7.0%}")
    lines.append(f"{'合计':<28}{total:>10,}")

    if prompt_tokens:
        lines.append(f"\n最后一次请求 {prompt_tokens:,} token，"
                     f"约 {total / prompt_tokens:.1f} 字符/token")
    return "\n".join(lines)