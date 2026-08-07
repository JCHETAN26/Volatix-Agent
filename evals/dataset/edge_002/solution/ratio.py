def rate(total, successes):
    if total == 0:
        return 0.0
    return successes / total * 100
