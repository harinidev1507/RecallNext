import pytest

from backend.config import ConfigurationError, ExasolConfig

VALID_ENVIRONMENT = {
    "EXASOL_DSN": "example.invalid:8563",
    "EXASOL_USER": "recallnext_app",
    "EXASOL_PASSWORD": "not-a-real-secret",
    "EXASOL_SCHEMA": "RECALLNEXT",
}


def test_requires_real_connection_values():
    with pytest.raises(ConfigurationError, match="EXASOL_DSN"):
        ExasolConfig.from_environment({})
    with pytest.raises(ConfigurationError, match="real value"):
        ExasolConfig.from_environment(
            {**VALID_ENVIRONMENT, "EXASOL_PASSWORD": "<database-password>"}
        )


def test_fixed_schema_prevents_script_and_runtime_mismatch():
    with pytest.raises(ConfigurationError, match="fixed Exasol schema"):
        ExasolConfig.from_environment(
            {**VALID_ENVIRONMENT, "EXASOL_SCHEMA": "OTHER_SCHEMA"}
        )


def test_parses_safe_defaults_and_redacts_password():
    config = ExasolConfig.from_environment(VALID_ENVIRONMENT)

    assert config.encryption is True
    assert config.compression is True
    assert config.query_timeout_seconds == 30
    assert config.safe_summary()["password"] == "<redacted>"
    assert "not-a-real-secret" not in repr(config.safe_summary())


def test_rejects_invalid_boolean_and_timeout():
    with pytest.raises(ConfigurationError, match="true or false"):
        ExasolConfig.from_environment(
            {**VALID_ENVIRONMENT, "EXASOL_ENCRYPTION": "perhaps"}
        )
    with pytest.raises(ConfigurationError, match="greater than zero"):
        ExasolConfig.from_environment(
            {**VALID_ENVIRONMENT, "EXASOL_QUERY_TIMEOUT_SECONDS": "0"}
        )


def test_rejects_insecure_certificate_bypass():
    with pytest.raises(ConfigurationError, match="certificate"):
        ExasolConfig.from_environment(
            {
                **VALID_ENVIRONMENT,
                "EXASOL_DSN": "example.invalid/nocertcheck:8563",
            }
        )
