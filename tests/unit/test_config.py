import pytest
from pydantic import ValidationError

from patchpilot.config import Settings


def test_defaults_never_enable_paid_calls() -> None:
    settings = Settings()

    assert settings.live is False
    assert settings.model_id == "claude-sonnet-5-5"
    assert settings.max_cost_usd == 5.0
    assert settings.run_cap_usd == 0.75


def test_environment_overrides_use_the_patchpilot_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATCHPILOT_MAX_ATTEMPTS", "5")

    assert Settings().max_attempts == 5


def test_the_token_is_a_secret_and_empty_values_mean_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATCHPILOT_GITHUB_TOKEN", "not-a-real-token")
    settings = Settings()
    assert settings.github_token is not None
    assert "not-a-real-token" not in repr(settings)
    assert "not-a-real-token" not in settings.model_dump_json()

    for empty in ("", "   "):
        monkeypatch.setenv("PATCHPILOT_GITHUB_TOKEN", empty)
        monkeypatch.setenv("PATCHPILOT_ALLOWLIST_PATH", empty)
        assert Settings().github_token is None
        assert Settings().allowlist_path is None


@pytest.mark.parametrize("value", ["0", "11"])
def test_attempt_budget_is_bounded(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("PATCHPILOT_MAX_ATTEMPTS", value)

    with pytest.raises(ValidationError):
        Settings()
