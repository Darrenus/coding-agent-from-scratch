"""SEARCH/REPLACE 的匹配策略链。

模型复述原文时会丢缩进、改空白。逐字符精确匹配因此经常失败，
而每次失败都要模型重来一轮，很贵。

策略从最严格开始逐级放宽。每一级都要求匹配唯一——
宁可报错让模型重试，也不能猜错位置静默改坏文件。
"""
import difflib
from typing import List, NamedTuple, Optional, Tuple

FUZZY_THRESHOLD = 0.85   # 相似度低于此值不接受
FUZZY_MARGIN = 0.05      # 最佳必须比次佳高出这么多，否则视为歧义
STRATEGY_NAMES = ("exact", "normalized", "indent", "fuzzy")

class Match(NamedTuple):
    start: int       # 在原文中的字符起点
    end: int         # 字符终点
    strategy: str    # 哪一级命中
    score: float     # 相似度
    shift: int       # new_str 需要平移的缩进量（正=加，负=减）


def _line_starts(lines: List[str]) -> List[int]:
    """每行在原文中的起始字符下标，末尾多一个哨兵。"""
    starts, pos = [], 0
    for line in lines:
        starts.append(pos)
        pos += len(line) + 1     # +1 是换行符
    starts.append(pos)
    return starts


def _common_indent(lines: List[str]) -> int:
    """一组行的公共缩进宽度，空行不参与计算。"""
    widths = [len(l) - len(l.lstrip()) for l in lines if l.strip()]
    return min(widths) if widths else 0


def _dedent(lines: List[str]) -> List[str]:
    pad = _common_indent(lines)
    return [l[pad:] if l.strip() else "" for l in lines]

def _normalize_with_map(text):
    """去掉每行行尾空白，并记录归一化后每个字符在原文中的下标。"""
    chars = []
    index_map = []
    pos = 0
    lines = text.split("\n")
    for n, line in enumerate(lines):
        stripped = line.rstrip()
        for i, ch in enumerate(stripped):
            chars.append(ch)
            index_map.append(pos + i)
        if n < len(lines) - 1:
            chars.append("\n")
            index_map.append(pos + len(line))   # 换行符在原文中的位置
        pos += len(line) + 1
    return "".join(chars), index_map

def shift_indent(text: str, delta: int) -> str:
    """把整段文本的缩进平移 delta 个空格。"""
    if delta == 0:
        return text
    out = []
    for line in text.split("\n"):
        if not line.strip():
            out.append(line)
        elif delta > 0:
            out.append(" " * delta + line)
        else:
            available = len(line) - len(line.lstrip())
            out.append(line[min(-delta, available):])
    return "\n".join(out)


def find_match(content: str, search: str) -> Tuple[Optional[Match], str]:
    """在 content 里定位 search。返回 (匹配, 失败原因)。"""
    if not search:
        return None, "search 为空"

    # ── 策略 1：逐字符精确 ────────────────────────────────
    hits = content.count(search)
    if hits == 1:
        start = content.index(search)
        return Match(start, start + len(search), "exact", 1.0, 0), ""
    if hits > 1:
        return None, f"精确匹配到 {hits} 处，无法确定改哪一处；请加上更多上下文"

    content_lines = content.split("\n")
    search_lines = search.split("\n")
    n = len(search_lines)
    if n > len(content_lines):
        return None, "search 比文件还长"

    starts = _line_starts(content_lines)
    windows = range(len(content_lines) - n + 1)

    def span(i):
        return starts[i], starts[i + n] - 1

    # ── 策略 2：忽略行尾空白 ──────────────────────────────
    norm_search = "\n".join(l.rstrip() for l in search_lines)
    if norm_search.strip():
        norm_content, index_map = _normalize_with_map(content)
        hits = norm_content.count(norm_search)
        if hits == 1:
            s = norm_content.index(norm_search)
            e = s + len(norm_search)
            return Match(index_map[s], index_map[e - 1] + 1, "normalized", 1.0, 0), ""
        if hits > 1:
            return None, f"忽略行尾空白后匹配到 {hits} 处；请加上更多上下文"

    # ── 策略 3：忽略整体缩进差异 ──────────────────────────
    target_flat = _dedent([l.rstrip() for l in search_lines])
    search_indent = _common_indent(search_lines)
    found = [i for i in windows
             if _dedent([l.rstrip() for l in content_lines[i:i + n]]) == target_flat]
    if len(found) == 1:
        i = found[0]
        a, b = span(i)
        window_indent = _common_indent(content_lines[i:i + n])
        return Match(a, b, "indent", 1.0, window_indent - search_indent), ""
    if len(found) > 1:
        return None, f"忽略缩进后仍匹配到 {len(found)} 处；请加上更多上下文"

    # ── 策略 4：模糊相似度 ────────────────────────────────
    matcher = difflib.SequenceMatcher(autojunk=False)
    matcher.set_seq2(search)
    scored = []
    for i in windows:
        window = "\n".join(content_lines[i:i + n])
        matcher.set_seq1(window)
        if matcher.real_quick_ratio() < FUZZY_THRESHOLD:
            continue
        if matcher.quick_ratio() < FUZZY_THRESHOLD:
            continue
        scored.append((matcher.ratio(), i))

    if not scored:
        return None, ("找不到匹配的内容。请先用 read_file 读出原文，"
                      "把 old_str 逐字符照抄过来。")

    scored.sort(reverse=True)
    best_score, best_i = scored[0]
    if best_score < FUZZY_THRESHOLD:
        return None, f"最相似的位置只有 {best_score:.2f}，低于阈值 {FUZZY_THRESHOLD}"

    runner_up = scored[1][0] if len(scored) > 1 else 0.0
    if best_score - runner_up < FUZZY_MARGIN:
        return None, (f"有两处相似度接近（{best_score:.2f} 和 {runner_up:.2f}），"
                      f"无法确定改哪一处；请加上更多上下文")

    a, b = span(best_i)
    window_indent = _common_indent(content_lines[best_i:best_i + n])
    return Match(a, b, "fuzzy", best_score,
                 window_indent - _common_indent(search_lines)), ""


def apply_edit(content: str, search: str, replace: str) -> Tuple[Optional[str], str]:
    """返回 (新内容, 说明)。失败时新内容为 None，说明里是原因。"""
    match, reason = find_match(content, search)
    if match is None:
        return None, reason
    new_text = shift_indent(replace, match.shift)
    new_content = content[:match.start] + new_text + content[match.end:]
    note = f"{match.strategy} 匹配"
    if match.score < 1.0:
        note += f"（相似度 {match.score:.2f}）"
    if match.shift:
        note += f"，缩进平移 {match.shift:+d}"
    return new_content, note