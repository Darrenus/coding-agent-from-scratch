import unittest

import shop


class ShopTests(unittest.TestCase):
    def setUp(self):
        self.cart = shop.Cart()
        self.cart.add("书", 30.0, 2)      # 60
        self.cart.add("笔", 5.0, 4)       # 20

    def test_item_count_sums_quantities(self):
        self.assertEqual(self.cart.item_count(), 6)

    def test_subtotal_multiplies_price_by_quantity(self):
        self.assertAlmostEqual(self.cart.subtotal(), 80.0)

    def test_tax_uses_the_tax_rate(self):
        self.assertAlmostEqual(self.cart.tax(), 80.0 * shop.TAX_RATE)

    def test_shipping_is_flat_under_threshold(self):
        self.assertAlmostEqual(self.cart.shipping(), 5.0)

    def test_total_includes_shipping(self):
        self.assertAlmostEqual(self.cart.total(), 80.0 + 80.0 * shop.TAX_RATE + 5.0)

    def test_cheapest_item(self):
        self.assertEqual(self.cart.cheapest(), "笔")

    def test_cheapest_on_empty_cart_is_none(self):
        self.assertIsNone(shop.Cart().cheapest())


if __name__ == "__main__":
    unittest.main()
