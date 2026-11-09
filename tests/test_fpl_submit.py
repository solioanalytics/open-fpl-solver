import pytest

from fpl_submit import create_authenticated_session, submit_team


def test_submission_without_confirmation_does_not_authenticate(monkeypatch):
    def fail_if_called(**kwargs):
        raise AssertionError("authentication must not run without explicit confirmation")

    monkeypatch.setattr("fpl_submit.create_authenticated_session", fail_if_called)

    assert submit_team(1, [], [], [], confirmation="") is False


def test_authentication_requires_live_confirmation():
    with pytest.raises(PermissionError, match="submit live"):
        create_authenticated_session(email="email@example.com", password="password")
