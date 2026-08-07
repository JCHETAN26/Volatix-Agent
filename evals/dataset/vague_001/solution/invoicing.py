def invoice_total(items):
    return sum(item["price"] for item in items)
