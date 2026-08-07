from reporting import success_rate


def test_new_account():
    assert success_rate([]) == 0.0


def test_existing_account():
    assert success_rate([1, 1, 0, 0]) == 50.0
