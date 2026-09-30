"""Cart.discount 的边界测试。不调用模型，不花钱。

背景：曾经有人以为 percent == 100 被当成非法，想把检查改成 `percent >= 100`。
实际 `> 100` 才是正确的拒绝边界，100 是合法的（打满折扣，结果为 0）。
这里用测试把这个契约钉死，避免"修好一个不存在的 bug"。
"""
import unittest

from cart import Cart


class DiscountBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.cart = Cart()
        self.cart.add("书", 100, 2)          # subtotal = 200

    def test_percent_100_is_legal(self):
        # 核心一条：100 必须放行，且结果为 0，而不是抛 ValueError。
        self.assertEqual(self.cart.discount(100), 0)

    def test_percent_0_is_legal_and_is_a_no_op(self):
        self.assertEqual(self.cart.discount(0), self.cart.total())

    def test_mid_range_is_linear(self):
        self.assertAlmostEqual(self.cart.discount(50), self.cart.total() * 0.5)

    def test_percent_above_100_is_refused(self):
        with self.assertRaises(ValueError):
            self.cart.discount(101)

    def test_negative_percent_is_refused(self):
        with self.assertRaises(ValueError):
            self.cart.discount(-1)

    def test_boundary_error_message_mentions_the_range(self):
        with self.assertRaises(ValueError) as ctx:
            self.cart.discount(100.1)
        self.assertIn("0", str(ctx.exception))
        self.assertIn("100", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
