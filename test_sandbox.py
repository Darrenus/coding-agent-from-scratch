"""沙箱逃逸测试。不调模型，不花钱。

策略文本看起来对不代表行为对——`(allow default)` 的写法意味着
「忘记 deny 的就是放行的」，所以必须实测行为。
"""
import os
import shlex
import subprocess
import tempfile
import unittest

import sandbox

@unittest.skipUnless(sandbox.available(), "当前平台没有 sandbox-exec")
class EscapeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.work = os.path.realpath(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def run_in_sandbox(self, command, timeout=20):
        argv = sandbox.wrap(command, self.work)
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return proc.stdout + proc.stderr

    def test_write_inside_workspace_is_allowed(self):
        out = self.run_in_sandbox(f"echo ok > {shlex.quote(self.work)}/a.txt && cat {shlex.quote(self.work)}/a.txt")
        self.assertIn("ok", out)

    def test_write_outside_workspace_is_blocked(self):
        probe = os.path.join(os.path.expanduser("~"), "sbx_escape_probe.txt")
        out = self.run_in_sandbox(f"echo bad > {shlex.quote(probe)}; echo DONE")
        self.assertIn("DONE", out, "命令根本没执行，这条测试没有意义")
        self.assertFalse(os.path.exists(probe), "沙箱外的文件被创建了！")

    def test_read_outside_workspace_is_allowed(self):
        # 必须允许——否则 python、系统库全都加载不了
        self.assertIn("#", self.run_in_sandbox("head -1 /etc/hosts"))

    NET_PROBE = (
        "import socket\n"
        "try:\n"
        "    socket.create_connection(('1.1.1.1', 443), timeout=4)\n"
        "    print('CONNECTED')\n"
        "except PermissionError:\n"
        "    print('EPERM')\n"
        "except Exception as e:\n"
        "    print(type(e).__name__)\n")

    def test_network_is_denied_by_the_sandbox(self):
        """断言拒绝的证据（EPERM），不是成功的缺席。

        原来这条写成「连不上就算通过」，结果断网时也通过——
        对照实验显示它在没有沙箱的情况下同样绿，等于没有测试。
        """
        self.assertIn("EPERM", self.run_in_sandbox(f"python3 -c {shlex.quote(self.NET_PROBE)}"))

    def test_network_probe_has_discriminating_power(self):
        """对照组：同一探针在沙箱外绝不能给出 EPERM。

        没有这条，上一条测试就可能在任何环境下恒绿而无人察觉。
        """
        proc = subprocess.run(["/bin/bash", "-c", f"python3 -c {shlex.quote(self.NET_PROBE)}"],
                              capture_output=True, text=True, timeout=30)
        self.assertNotIn("EPERM", proc.stdout + proc.stderr)
    
    def test_pip_user_install_is_blocked(self):
        """真实事故复现：agent 曾经自己 pip install --user 装包到用户环境。"""
        user_site = self.run_in_sandbox(
            "python3 -c \"import site; print(site.getusersitepackages())\"").strip().splitlines()[-1]
        out = self.run_in_sandbox(f"touch {shlex.quote(user_site)}/sbx_probe.py")
        self.assertIn("not permitted", out.lower())

    def test_python_still_works(self):
        self.assertIn("2", self.run_in_sandbox("python3 -c 'print(1+1)'"))


class ProfileTests(unittest.TestCase):
    def test_quote_in_path_is_refused(self):
        # 路径拼进策略文本，引号能注入任意规则——宁可拒绝也不转义
        with self.assertRaises(ValueError):
            sandbox.profile('/tmp/evil" (allow file-write*) (subpath "/')

    def test_network_rule_is_togglable(self):
        self.assertIn("(deny network*)", sandbox.profile("/tmp"))
        self.assertNotIn("(deny network*)", sandbox.profile("/tmp", allow_network=True))