"""一个小购物车模块。"""   


class Cart:   
    TAX_RATE = 0.05   

    def __init__(self):   
        self.items = []   

    def add(self, name, price, qty=1):   
        if qty <= 0:   
            raise ValueError("数量必须为正")   
        self.items.append({"name": name, "price": price, "qty": qty})   

    def subtotal(self):   
        total = 0   
        for item in self.items:   
            total += item["price"] * item["qty"]   
        return total   

    def tax(self):   
        return self.subtotal() * self.TAX_RATE   

    def total(self):   
        return self.subtotal() + self.tax()   

    def discount(self, percent):   
        if percent < 0 or percent > 100:   
            raise ValueError("折扣必须在 0 到 100 之间")   
        return self.total() * (1 - percent / 100)   