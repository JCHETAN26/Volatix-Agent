def success_rate(runs):
    if not runs:
        return 0.0
    return round(100 * sum(runs) / len(runs), 1)
