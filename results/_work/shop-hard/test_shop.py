import unittest

from shop import Cart


def fresh():
    """显式传入新列表，隔离「可变默认参数」那个 bug 对其他测试的干扰。"""
    return Cart([])


class CartTests(unittest.TestCase):
    def test_two_carts_do_not_share_items(self):
        a, b = Cart(), Cart()
        a.add("书", 10.0)
        self.assertEqual(len(b.items), 0)

    def test_has_item_compares_by_value(self):
        cart = fresh()
        cart.add("apple pie", 3.0)
        name = " ".join(["apple", "pie"])      # 运行期拼接，不会被驻留
        self.assertTrue(cart.has_item(name))

    def test_coupon_is_deducted_before_tax(self):
        cart = fresh()
        cart.add("书", 100.0)
        # 先扣 20 优惠券再加 8% 税：(100 - 20) * 1.08 = 86.4
        self.assertAlmostEqual(cart.total(coupon=20.0), 86.4, places=2)

    def test_coupon_cannot_make_total_negative(self):
        cart = fresh()
        cart.add("糖", 1.0)
        self.assertAlmostEqual(cart.total(coupon=999.0), 0.0, places=2)

    def test_no_coupon_still_adds_tax(self):
        cart = fresh()
        cart.add("书", 100.0)
        self.assertAlmostEqual(cart.total(), 108.0, places=2)

    def test_receipt_rounds_to_two_decimals(self):
        cart = fresh()
        cart.add("糖", 0.1, 3)
        self.assertEqual(cart.receipt_line(0), "糖 x3 = 0.30")


if __name__ == "__main__":
    unittest.main()
