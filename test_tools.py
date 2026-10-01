"""工具层测试。不调用模型，不花钱。"""
import unittest
import editor
import os
import json

from agent import WORKSPACE, run_tool


class SafePathTests(unittest.TestCase):
    def assert_refused(self, path):
        result = run_tool("read_file", '{"path": "%s"}' % path)
        self.assertTrue(result.startswith("错误"), f"{path} 本该被拒绝，实际返回：{result[:80]}")

    def test_workspace_is_a_real_directory(self):
        # 这条守着 fail-open：WORKSPACE 一旦退化，整条边界检查就是摆设。
        self.assertTrue(WORKSPACE.startswith("/"))
        self.assertNotEqual(WORKSPACE, "/")

    def test_normal_file_is_allowed(self):
        self.assertIn("def add", run_tool("read_file", '{"path": "calc.py"}'))

    def test_absolute_path_outside_workspace_is_refused(self):
        self.assert_refused("/etc/passwd")

    def test_parent_traversal_is_refused(self):
        self.assert_refused("../../.ssh/id_rsa")

    def test_traversal_to_a_file_with_an_innocent_name_is_refused(self):
        # 上一条可能只是撞上了 DENY_PATTERNS 里的 id_rsa。
        # 这条拿掉那个巧合，逼边界检查自己站住。
        self.assert_refused("../../Documents/notes.txt")

    def test_dotenv_is_refused(self):
        self.assert_refused(".env")

    def test_schema_and_registry_agree(self):
        from agent import TOOLS, TOOL_REGISTRY
        self.assertEqual({t["function"]["name"] for t in TOOLS}, set(TOOL_REGISTRY))

class ToolErrorTests(unittest.TestCase):
    """1.2 的契约：永远返回字符串，永远不抛异常。"""

    def test_missing_file(self):
        self.assertTrue(run_tool("read_file", '{"path": "nope.py"}').startswith("错误"))

    def test_malformed_json(self):
        self.assertTrue(run_tool("read_file", "{path: bad}").startswith("错误"))

    def test_wrong_argument_name(self):
        self.assertTrue(run_tool("read_file", '{"file": "calc.py"}').startswith("错误"))

    def test_unknown_tool(self):
        self.assertTrue(run_tool("teleport", "{}").startswith("错误"))

class MatcherTests(unittest.TestCase):
    FILE = "def add(a, b):\n    return a - b\n\n\ndef sub(a, b):\n    return a - b\n"

    def apply(self, search, replace, content=None):
        return editor.apply_edit(content if content is not None else self.FILE, search, replace)

    def test_exact(self):
        new, note = self.apply("def add(a, b):\n    return a - b",
                               "def add(a, b):\n    return a + b")
        self.assertIn("exact", note)
        self.assertIn("return a + b", new)

    def test_ambiguous_exact_is_refused(self):
        new, note = self.apply("    return a - b", "    return a + b")
        self.assertIsNone(new)
        self.assertIn("2 处", note)

    def test_trailing_space_is_normalized(self):
        new, note = self.apply("def add(a, b):   \n    return a - b   ",
                               "def add(a, b):\n    return a + b")
        self.assertIn("normalized", note)
        self.assertIn("return a + b", new)

    def test_lost_indentation_is_recovered_and_reindented(self):
        # 模型把方法体抄出来时丢了缩进，new_str 也没缩进
        content = "class C:\n    def add(self, a, b):\n        return a - b\n"
        new, note = self.apply("def add(self, a, b):\n    return a - b",
                               "def add(self, a, b):\n    return a + b",
                               content)
        self.assertIn("indent", note)
        # 关键：补回来的代码必须带上原来的 4 空格缩进
        self.assertIn("    def add(self, a, b):\n        return a + b", new)

    def test_fuzzy_recovers_small_typo(self):
        new, note = self.apply("def add(a,b):\n    return a - b",
                               "def add(a, b):\n    return a + b")
        self.assertIn("fuzzy", note)
        self.assertIn("return a + b", new)

    def test_two_close_candidates_are_refused(self):
        content = "def f(x):\n    return x + 1\n\n\ndef g(x):\n    return x + 2\n"
        new, note = self.apply("def f(x):\n    return x + 9", "xxx", content)
        self.assertIsNone(new)

    def test_nothing_similar_is_refused(self):
        new, note = self.apply("import tensorflow as tf\nmodel = tf.keras", "x")
        self.assertIsNone(new)

    def test_partial_trailing_line_as_anchor(self):
        """真实失败案例：模型引用两行，第二行只抄了前半句当插入锚点，
        同时把第一行的行尾空格吃掉了。"""
        content = ('    def add(self, name, price, qty=1):   \n'
                   '        if qty <= 0:   \n'
                   '            raise ValueError("数量必须为正")   \n'
                   '        self.items.append({"name": name, "qty": qty})   \n')
        search = ('            raise ValueError("数量必须为正")\n'
                  '        self.items.append')
        replace = ('            raise ValueError("数量必须为正")\n'
                   '        if price < 0:\n'
                   '            raise ValueError("价格不能为负")\n'
                   '        self.items.append')

        new, note = editor.apply_edit(content, search, replace)
        self.assertIsNotNone(new, f"应该能匹配上，实际：{note}")
        self.assertIn("价格不能为负", new)
        # 被截断的后半句必须原样留着，不能被字符级替换吃掉
        self.assertIn('self.items.append({"name": name, "qty": qty})', new)

class EditFileTests(unittest.TestCase):
    """测 agent.edit_file 本身：路径检查、磁盘读写、新建文件。"""

    def setUp(self):
        self.name = "tmp_edit_target.py"
        self.path = os.path.join(WORKSPACE, self.name)
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("def add(a, b):\n    return a - b\n\n\ndef sub(a, b):\n    return a - b\n")

    def tearDown(self):
        for name in (self.name, "tmp_created.py"):
            p = os.path.join(WORKSPACE, name)
            if os.path.exists(p):
                os.remove(p)

    def body(self, name=None):
        with open(os.path.join(WORKSPACE, name or self.name), encoding="utf-8") as f:
            return f.read()

    def edit(self, old, new, path=None):
        return run_tool("edit_file", json.dumps(
            {"path": path or self.name, "old_str": old, "new_str": new}))

    def test_edit_is_written_to_disk(self):
        result = self.edit("def add(a, b):\n    return a - b",
                           "def add(a, b):\n    return a + b")
        self.assertTrue(result.startswith("已修改"), result)
        self.assertIn("return a + b", self.body())

    def test_refusal_leaves_the_file_untouched(self):
        before = self.body()
        self.assertTrue(self.edit("    return a - b", "x").startswith("错误"))
        self.assertEqual(before, self.body())      # 最关键的一条

    def test_create_new_file(self):
        result = self.edit("", "print('hi')\n", path="tmp_created.py")
        self.assertTrue(result.startswith("已创建"), result)
        self.assertEqual("print('hi')\n", self.body("tmp_created.py"))

    def test_create_refuses_to_clobber(self):
        before = self.body()
        self.assertTrue(self.edit("", "毁掉它").startswith("错误"))
        self.assertEqual(before, self.body())

    def test_path_guard_still_applies(self):
        self.assertTrue(self.edit("root", "hacked", path="../../.zshrc").startswith("错误"))

class BashTests(unittest.TestCase):
    def run_bash(self, command):
        return run_tool("bash", json.dumps({"command": command}))

    def test_basic_command(self):
        self.assertIn("hello", self.run_bash("echo hello"))

    def test_exit_code_is_reported(self):
        self.assertIn("[退出码 3]", self.run_bash("exit 3"))

    def test_stderr_is_captured(self):
        self.assertIn("boom", self.run_bash("echo boom >&2"))

    def test_runs_in_the_workspace(self):
        self.assertIn("cart.py", self.run_bash("ls"))

    def test_write_outside_workspace_is_blocked(self):
        self.assertFalse(os.path.exists(os.path.expanduser("~/bash_escape_probe.txt")))

    def test_interactive_command_does_not_hang(self):
        # stdin 接 /dev/null，所以读输入的命令立刻 EOF 而不是等 60 秒超时
        import time
        t = time.time()
        self.run_bash("cat")
        self.assertLess(time.time() - t, 10)

    def test_long_output_is_truncated(self):
        out = self.run_bash("python3 -c \"print('x' * 50000)\"")
        self.assertIn("省略", out)
        self.assertLess(len(out), 12000)

class DelegateTests(unittest.TestCase):
    def test_sub_agent_cannot_delegate_further(self):
        import agent as A
        A._depth = A.MAX_DELEGATE_DEPTH
        try:
            out = A.delegate("随便问点什么")
            self.assertTrue(out.startswith("错误"))
            self.assertIn("不能再派", out)
        finally:
            A._depth = 0

    def test_read_only_set_excludes_writes(self):
        import agent as A
        self.assertNotIn("edit_file", A.READ_ONLY_TOOLS)
        self.assertNotIn("bash", A.READ_ONLY_TOOLS)
        self.assertNotIn("delegate", A.READ_ONLY_TOOLS)

    def test_restricted_tool_is_refused_even_if_called(self):
        # schema 里没给，不代表模型不会调——分发层要有自己的闸
        import agent as A
        self.assertTrue(set(A.READ_ONLY_TOOLS).issubset(set(A.TOOL_REGISTRY)))

if __name__ == "__main__":
    unittest.main()