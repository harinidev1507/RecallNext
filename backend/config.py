"""Validated Exasol connection configuration.

There are deliberately no working default credentials. A missing or malformed
database target is an error so a demo can never silently fall back to mock data.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass

_SCHEMA_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")


class ConfigurationError(ValueError):
    """Raised when the real Exasol target is not configured safely."""


def _required(environment: Mapping[str, str], name: str) -> str:
    value = environment.get(name, "").strip()
    if not value or value.startswith("<"):
        raise ConfigurationError(f"{name} must be set to a real value")
    return value


def _boolean(environment: Mapping[str, str], name: str, default: bool) -> bool:
    raw = environment.get(name)
    if raw is None or not raw.strip():
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigurationError(f"{name} must be true or false")


def _positive_integer(environment: Mapping[str, str], name: str, default: int) -> int:
    raw = environment.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError as error:
        raise ConfigurationError(f"{name} must be an integer") from error
    if value <= 0:
        raise ConfigurationError(f"{name} must be greater than zero")
    return value


@dataclass(frozen=True)
class ExasolConfig:
    dsn: str
    user: str
    password: str
    schema: str = "RECALLNEXT"
    encryption: bool = True
    compression: bool = True
    query_timeout_seconds: int = 30

    @classmethod
    def from_environment(
        cls, environment: Mapping[str, str] | None = None
    ) -> ExasolConfig:
        values = os.environ if environment is None else environment
        schema = values.get("EXASOL_SCHEMA", "RECALLNEXT").strip().upper()
        if not _SCHEMA_PATTERN.fullmatch(schema):
            raise ConfigurationError(
                "EXASOL_SCHEMA must be an unquoted Exasol identifier"
            )
        if schema != "RECALLNEXT":
            raise ConfigurationError(
                "contract version 1 uses the fixed Exasol schema RECALLNEXT"
            )
        dsn = _required(values, "EXASOL_DSN")
        if ":" not in dsn:
            raise ConfigurationError(
                "EXASOL_DSN must include the host and port reported by `exasol info`"
            )
        if "/nocertcheck" in dsn.lower():
            raise ConfigurationError(
                "EXASOL_DSN must validate the server certificate; use its SHA-256 "
                "fingerprint instead of /nocertcheck"
            )
        return cls(
            dsn=dsn,
            user=_required(values, "EXASOL_USER"),
            password=_required(values, "EXASOL_PASSWORD"),
            schema=schema,
            encryption=_boolean(values, "EXASOL_ENCRYPTION", True),
            compression=_boolean(values, "EXASOL_COMPRESSION", True),
            query_timeout_seconds=_positive_integer(
                values, "EXASOL_QUERY_TIMEOUT_SECONDS", 30
            ),
        )

    def safe_summary(self) -> dict[str, object]:
        """Return diagnostics that can be logged without exposing credentials."""

        return {
            "dsn": self.dsn,
            "user": self.user,
            "password": "<redacted>",
            "schema": self.schema,
            "encryption": self.encryption,
            "compression": self.compression,
            "query_timeout_seconds": self.query_timeout_seconds,
        }
