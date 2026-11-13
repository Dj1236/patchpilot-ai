"""Guards that apply to every test."""

import pytest


@pytest.fixture(autouse=True)
def no_paid_model_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make a paid model call impossible from any test.

    Credentials are removed from the environment and the SDK is pointed at a
    local address nothing listens on, so even a test that built a real client
    by mistake could not reach the provider.
    """
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:9")
    # Without a key the Google SDK refuses to build a client at all.
    for name in (
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "GOOGLE_GENAI_USE_VERTEXAI",
        "GOOGLE_GENAI_USE_ENTERPRISE",
        "PATCHPILOT_PROVIDER",
        "PATCHPILOT_GEMINI_MODEL",
        "PATCHPILOT_GEMINI_FREE_TIER_CONFIRMED",
    ):
        monkeypatch.delenv(name, raising=False)
