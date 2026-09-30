"""工具层测试。不调用模型，不花钱。"""
import unittest

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



if __name__ == "__main__":
    unittest.main()