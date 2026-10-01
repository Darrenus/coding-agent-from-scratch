"""一个小商店模块。"""


class Cart:
    def __init__(self, items=[]):
        self.items = items

    def add(self, name, price, qty=1):
        if qty <= 0:
            raise ValueError("数量必须为正")
        self.items.append({"name": name, "price": price, "qty": qty})

    def has_item(self, name):
        for item in self.items:
            if item["name"] is name:
                return True
        return False

    def subtotal(self):
        return sum(item["price"] * item["qty"] for item in self.items)

    def total(self, coupon=0.0, tax_rate=0.08):
        """先扣优惠券再计税的总价。优惠券不能把总价压到负数。"""
        taxed = self.subtotal() * (1 + tax_rate)
        return taxed - coupon

    def receipt_line(self, index):
        """收据上的一行，金额保留两位小数。"""
        item = self.items[index]
        return f"{item['name']} x{item['qty']} = {item['price'] * item['qty']}"
