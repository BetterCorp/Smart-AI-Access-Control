from backend.app.auth.simple import AdminAuth


def test_admin_auth_bootstrap_verify_and_session(tmp_path) -> None:
    auth = AdminAuth(tmp_path / "auth.json", iterations=10)

    auth.bootstrap("a-long-enough-password")
    token = auth.create_session()

    assert auth.is_bootstrapped()
    assert auth.verify("a-long-enough-password")
    assert not auth.verify("wrong-password")
    assert auth.has_session(token)

    auth.logout(token)

    assert not auth.has_session(token)

