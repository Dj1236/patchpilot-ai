import pytest
from pydantic import ValidationError

from patchpilot.config import Settings


def test_defaults_never_enable_paid_calls() -> None:
    settings = Settings()

    assert settings.live is False
    assert settings.model_id == "claude-sonnet-5-5"
    assert settings.max_cost_usd == 10.0


def test_environment_overrides_use_the_patchpilot_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATCHPILOT_MAX_ATTEMPTS", "5")

    assert Settings().max_attempts == 5


@pytest.mark.parametrize("value", ["0", "11"])
def test_attempt_budget_is_bounded(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("PATCHPILOT_MAX_ATTEMPTS", value)

    with pytest.raises(ValidationError):
        Settings()
