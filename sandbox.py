"""用 macOS Seatbelt 把命令关进沙箱。

审批回答的是「我同意你做这件事」，沙箱回答的是「即使我同意了你也跑不出去」。
一条长命令里藏了什么，人扫一眼看不出来；连按十次 yes 之后，第十一次不会细看。
危险能力需要一层不依赖人类注意力的约束。
"""
import os
import platform
import shutil

PROFILE_TEMPLATE = """(version 1)
(allow default)
(deny file-write*)
(allow file-write* (subpath {workspace}))
(allow file-write* (subpath "/private/tmp") (subpath "/private/var/folders"))
(allow file-write-data
  (literal "/dev/null") (literal "/dev/stdout") (literal "/dev/stderr")
  (literal "/dev/dtracehelper") (literal "/dev/tty"))
{network}
"""


def available() -> bool:
    """当前平台有没有可用的沙箱。"""
    return platform.system() == "Darwin" and shutil.which("sandbox-exec") is not None


def _sbpl_string(path: str) -> str:
    """把路径编码成 SBPL 字符串字面量。

    路径是拼进策略文本的——如果它能带引号或反斜杠进来，就能提前闭合字符串、
    注入任意规则，等于沙箱形同虚设。这是典型的注入面，宁可拒绝也不转义。
    """
    if '"' in path or "\\" in path or "\n" in path:
        raise ValueError(f"路径含有无法安全编码的字符，拒绝构造策略：{path!r}")
    return f'"{path}"'


def profile(workspace: str, allow_network: bool = False, deny_read=()) -> str:
    denied = "\n".join(
        f"(deny file-read* (subpath {_sbpl_string(os.path.realpath(p))}))"
        for p in deny_read
    )
    return PROFILE_TEMPLATE.format(
        workspace=_sbpl_string(os.path.realpath(workspace)),
        network="" if allow_network else "(deny network*)",
    ) + ("\n" + denied if denied else "")


def wrap(command: str, workspace: str, allow_network: bool = False, deny_read=()):
    """返回一个 argv；执行它等价于在沙箱里跑 command。沙箱不可用时返回 None。"""
    if not available():
        return None
    return ["sandbox-exec", "-p", profile(workspace, allow_network, deny_read),
            "/bin/bash", "-c", command]