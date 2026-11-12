"""Validated runtime settings for PatchPilot AI.

Settings are read from environment variables prefixed with ``PATCHPILOT_``.
Secrets are deliberately absent here; they are added with the components
that need them and are never written to logs or passed to a sandbox.
"""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Run-wide limits and model selection."""

    model_config = SettingsConfigDict(env_prefix="PATCHPILOT_", frozen=True, extra="forbid")

    model_id: str = Field(default="claude-sonnet-5-5", min_length=1)
    live: bool = Field(
        default=False,
        description="Paid model calls are made only when this is explicitly enabled.",
    )
    max_attempts: int = Field(default=3, ge=1, le=10)
    max_cost_usd: float = Field(default=10.0, gt=0)
