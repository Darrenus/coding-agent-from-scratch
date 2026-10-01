"""从源码里抽取符号：谁定义了什么，谁引用了什么。

这是 repo map 的第一步。有了「定义」和「引用」，才能把文件连成一张图，
进而回答「这个任务最该看哪些代码」。
"""
import os
import re

from typing import List, NamedTuple

import math
from collections import Counter

import tree_sitter_python as tsp
from tree_sitter import Language, Parser

PY_LANGUAGE = Language(tsp.language())
_parser = Parser(PY_LANGUAGE)

# S-表达式查询：括号里是节点类型，冒号前是字段名，@ 后面是给捕获起的名字。
# 读法：「找 function_definition 节点，取它 name 字段里的 identifier，标记为 def」
QUERY = PY_LANGUAGE.query("""
(function_definition name: (identifier) @def)
(class_definition    name: (identifier) @def)
(call function: (identifier) @ref)
(call function: (attribute attribute: (identifier) @ref))
(import_statement name: (dotted_name) @import)
(import_statement name: (aliased_import name: (dotted_name) @import))
(import_from_statement module_name: (dotted_name) @import)
(import_from_statement module_name: (relative_import) @import)
""")


class Tag(NamedTuple):
    name: str     # 符号名
    kind: str     # "def" 或 "ref"
    line: int     # 1-based 行号


def tags_for_source(source: bytes) -> List[Tag]:
    """从源码字节串里抽出所有符号。语法有错也能尽量解析。"""
    tree = _parser.parse(source)
    tags = []
    for capture_name, nodes in QUERY.captures(tree.root_node).items():
        for node in nodes:
            tags.append(Tag(
                name=node.text.decode("utf-8", errors="replace"),
                kind=capture_name,
                line=node.start_point[0] + 1,
            ))
    return sorted(tags, key=lambda t: (t.line, t.kind, t.name))


def tags_for_file(path: str) -> List[Tag]:
    with open(path, "rb") as f:
        return tags_for_source(f.read())


def defines(tags: List[Tag]):
    """这个文件定义了哪些符号。"""
    return {t.name for t in tags if t.kind == "def"}


def references(tags: List[Tag]):
    """这个文件引用了哪些符号。"""
    return {t.name for t in tags if t.kind == "ref"}

def imports(tags: List[Tag]):
    """这个文件导入了哪些模块。"""
    return {t.name for t in tags if t.kind == "import"}


def module_name(path: str, root: str = ".") -> str:
    """文件路径 → Python 模块名。

    包根目录 = 从文件所在目录往上走、第一个不含 __init__.py 的目录。
    所以 src/pkg/mod.py 得到 pkg.mod 而不是 src.pkg.mod——
    包的根目录不一定是仓库的根目录。
    """
    full = os.path.join(root, path)
    base = os.path.basename(full)
    parts = [] if base == "__init__.py" else [base[:-3]]

    directory = os.path.dirname(full)
    while os.path.isfile(os.path.join(directory, "__init__.py")):
        parts.insert(0, os.path.basename(directory))
        parent = os.path.dirname(directory)
        if parent == directory:        # 防止走到文件系统根部死循环
            break
        directory = parent

    return ".".join(parts)


EXCLUDE_DIRS = {".git", "__pycache__", ".venv", "node_modules", ".pytest_cache"}


def scan_repo(root="."):
    """遍历仓库里的 .py 文件，返回 {相对路径: [Tag, ...]}。"""
    result = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if d not in EXCLUDE_DIRS and not d.startswith(".")]
        for name in sorted(filenames):
            if not name.endswith(".py"):
                continue
            full = os.path.join(dirpath, name)
            try:
                result[os.path.relpath(full, root)] = tags_for_file(full)
            except OSError:
                continue
    return result


def build_graph(file_tags, root=".", use_imports=True):
    """把文件连成有向图：A 引用了 B 定义的符号，就有一条 A → B 的边。

    use_imports=True 时额外要求 A 真的 import 过 B——Python 里不导入就用不了，
    所以这是一条硬约束，能滤掉大量同名符号造成的假边。

    返回 (edges, definers)：
      edges    {(引用方, 定义方): [共享的符号，可重复]}
      definers {符号: {定义它的文件集合}}
    """
    definers = {}
    for path, tags in file_tags.items():
        for name in defines(tags):
            definers.setdefault(name, set()).add(path)

    modules = {path: module_name(path, root) for path in file_tags}
    imported = {path: imports(tags) for path, tags in file_tags.items()}

    edges = {}
    for path, tags in file_tags.items():
        for tag in tags:
            if tag.kind != "ref":
                continue
            for target in definers.get(tag.name, ()):
                if target == path:
                    continue
                if use_imports and modules[target] not in imported[path]:
                    continue
                edges.setdefault((path, target), []).append(tag.name)

    return edges, definers

def edge_weights(edges, definers):
    """把「共享了哪些符号」折算成数值权重。

    符号越稀有，信息量越大：一个只有一处定义的符号，指向关系是确定的；
    一个三处都有定义的符号，这条边只有三分之一的可信度。
    """
    weights = {}
    for key, syms in edges.items():
        weights[key] = sum(1.0 / len(definers.get(s, (key[1],))) for s in syms)
    return weights


def pagerank(nodes, weights, personalization=None,
             damping=0.85, iterations=100, tol=1e-10):
    """带个性化向量的 PageRank（幂迭代）。

    nodes           节点列表
    weights         {(src, dst): 权重}
    personalization {node: 权重}，瞬移时的落点分布；不给则均匀
    """
    nodes = list(nodes)
    if not nodes:
        return {}

    # 瞬移分布：归一化成概率
    if personalization and sum(personalization.get(x, 0.0) for x in nodes) > 0:
        total = sum(personalization.get(x, 0.0) for x in nodes)
        seed = {x: personalization.get(x, 0.0) / total for x in nodes}
    else:
        seed = {x: 1.0 / len(nodes) for x in nodes}

    out = {}
    for (src, _dst), w in weights.items():
        out[src] = out.get(src, 0.0) + w

    rank = dict(seed)
    for _ in range(iterations):
        new = {x: 0.0 for x in nodes}

        # 悬挂节点（没有出边）的分数会凭空消失，要收集起来重新分配
        leaked = sum(rank[x] for x in nodes if out.get(x, 0.0) == 0.0)

        for (src, dst), w in weights.items():
            new[dst] += damping * rank[src] * w / out[src]

        for x in nodes:
            new[x] += damping * leaked * seed[x] + (1.0 - damping) * seed[x]

        delta = sum(abs(new[x] - rank[x]) for x in nodes)
        rank = new
        if delta < tol:
            break

    return rank

def _split_identifier(name: str):
    """把标识符拆成小写词：get_swebench_docker_image_name → {get, swebench, docker, image, name}
    DockerEnvironment → {docker, environment}"""
    words = []
    for part in re.split(r"[_\W]+", name):
        words.extend(re.findall(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+", part))
    return {w.lower() for w in words if w}

def _path_words(path: str):
    """路径拆成小写词：src/minisweagent/models/litellm_model.py
    → {src, minisweagent, models, litellm, model}"""
    words = set()
    for part in re.split(r"[/\\.]+", path):
        if part and part != "py":
            words |= _split_identifier(part) or {part.lower()}
    return words


def task_personalization(task: str, file_tags, root: str = ".", min_len: int = 3):
    """从任务描述抽词，给相关文件打种子分。

    每个任务词对一个文件最多贡献一次（取最高档位），再乘以该词的 IDF。
    这样既不被文件大小左右，也不被 model / agent 这类泛词淹没。

      档位 3  词出现在路径里      （swebench → run/benchmarks/swebench.py）
      档位 2  词就是某个符号名     （litellm_model → LitellmModel）
      档位 1  词出现在符号的拆词里 （docker → DockerEnvironment）
    """
    task_words = {w.lower() for w in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", task)
                  if len(w) >= min_len}
    if not task_words:
        return {}

    # 每个文件涉及的词，以及档位
    tiers = {}
    for path, tags in file_tags.items():
        symbols = defines(tags)
        exact = {s.lower() for s in symbols}
        split = set()
        for s in symbols:
            split |= _split_identifier(s)
        here = {}
        for w in task_words:
            if w in _path_words(path):
                here[w] = 3.0
            elif w in exact:
                here[w] = 2.0
            elif w in split:
                here[w] = 1.0
        tiers[path] = here

    # IDF：词出现在越多文件里，信息量越低
    doc_freq = Counter()
    for here in tiers.values():
        for w in here:
            doc_freq[w] += 1
    n = len(file_tags)
    idf = {w: math.log(1 + n / (1 + doc_freq[w])) for w in task_words}

    seed = {}
    for path, here in tiers.items():
        score = sum(tier * idf[w] for w, tier in here.items())
        if score > 0:
            seed[path] = score
    return seed

def symmetrize(weights):
    """把有向图变成双向：既关心「我依赖谁」也关心「谁依赖我」。"""
    out = {}
    for (s, d), w in weights.items():
        out[(s, d)] = out.get((s, d), 0.0) + w
        out[(d, s)] = out.get((d, s), 0.0) + w
    return out

def render_map(file_tags, rank, max_files=12, max_symbols=10):
    """把排序结果渲染成给模型看的「仓库地图」。"""
    lines = ["仓库里与当前任务最相关的文件（按相关性排序）："]
    for path, _score in sorted(rank.items(), key=lambda kv: -kv[1])[:max_files]:
        tags = file_tags[path]
        syms = sorted({t.name for t in tags if t.kind == "def"})
        shown = syms[:max_symbols]
        more = f" …另有 {len(syms) - len(shown)} 个" if len(syms) > len(shown) else ""
        lines.append(f"  {path}" + (f": {', '.join(shown)}{more}" if shown else ""))
    return "\n".join(lines)