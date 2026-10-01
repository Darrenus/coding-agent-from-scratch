"""一个小商店模块。"""

TAX_RATE = 0.08
SHIPPING_FLAT = 5.0
FREE_SHIPPING_OVER = 100.0


class Cart:
    def __init__(self):
        self.items = []

    def add(self, name, price, qty=1):
        if qty <= 0:
            raise ValueError("数量必须为正")
        self.items.append({"name": name, "price": price, "qty": qty})

    def item_count(self):
        """购物车里商品的总件数。"""
        return sum(item["qty"] for item in self.items)

    def subtotal(self):
        total = 0.0
        for item in self.items:
            total += item["price"] * item["qty"]
        return total

    def tax(self):
        return self.subtotal() * TAX_RATE

    def shipping(self):
        if self.subtotal() > FREE_SHIPPING_OVER:
            return 0.0
        return SHIPPING_FLAT

    def total(self):
        return self.subtotal() + self.tax() + self.shipping()

    def cheapest(self):
        """返回单价最低的商品名。"""
        if not self.items:
            return None
        return min(self.items, key=lambda i: i["price"])["name"]
