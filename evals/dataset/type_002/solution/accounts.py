def display_name(nickname, fallback):
    if nickname is None:
        return fallback
    return nickname.strip() or fallback
