from pathlib import Path

from gefion.config import Settings, _parse_env_file, load_settings


def test_parse_env_file_reads_key_values(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "# comment",
                "ALPHAVANTAGE_API_KEY=abc123",
                "DATABASE_URL=postgres://user:pass@localhost:5432/db",
            ]
        )
    )

    parsed = _parse_env_file(env_file)

    assert parsed["ALPHAVANTAGE_API_KEY"] == "abc123"
    assert parsed["DATABASE_URL"].startswith("postgres://")


def test_load_settings_prefers_env_over_file(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("ALPHAVANTAGE_API_KEY=filevalue\nDATABASE_URL=filedb\n")

    settings = load_settings(
        env={"ALPHAVANTAGE_API_KEY": "envvalue"},
        env_file=env_file,
        include_os_env=False,
    )

    assert settings.alphavantage_api_key == "envvalue"
    assert settings.database_url == "filedb"
    assert settings.env_file == env_file


def test_settings_repr_masks_api_key() -> None:
    settings = Settings(alphavantage_api_key="secret", database_url=None)

    representation = repr(settings)

    assert "secret" not in representation
    assert "***" in representation
