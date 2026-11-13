"""Validated runtime settings for PatchPilot AI.

Settings are read from environment variables prefixed with ``PATCHPILOT_``.
The GitHub token is the only secret. It is held as a secret type, is never
written to logs, and is never passed to git or to a sandbox.
"""

from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from patchpilot.llm.base import Provider


class Settings(BaseSettings):
    """Run-wide limits and model selection."""

    model_config = SettingsConfigDict(env_prefix="PATCHPILOT_", frozen=True, extra="forbid")

    model_id: str = Field(default="claude-sonnet-5-5", min_length=1)
    provider: Provider = Field(
        default="anthropic",
        description="Model provider. Each provider has exactly one pinned model.",
    )
    gemini_free_tier_confirmed: bool = Field(
        default=False,
        description=(
            "The owner's statement that the Gemini key belongs to a project with no "
            "billing account. Code cannot check this, so live Gemini runs need it."
        ),
    )
    live: bool = Field(
        default=False,
        description="Paid model calls are made only when this is explicitly enabled.",
    )
    max_attempts: int = Field(default=3, ge=1, le=10)
    max_cost_usd: float = Field(
        default=5.0,
        gt=0,
        description="Total spend allowed across all runs recorded in the ledger.",
    )
    run_cap_usd: float = Field(default=0.75, gt=0, description="Spend allowed for one run.")
    runs_dir: Path = Field(default=Path("runs"), description="Where traces and reports go.")
    allowlist_path: Path | None = Field(
        default=None,
        description="TOML file of approved repositories. Without it nothing is approved.",
    )
    github_token: SecretStr | None = Field(
        default=None,
        description="Optional read-only token, sent to api.github.com only. Never to git.",
    )

    @field_validator("github_token", "allowlist_path", mode="before")
    @classmethod
    def _empty_means_unset(cls, value: object) -> object:
        return None if isinstance(value, str) and not value.strip() else value
