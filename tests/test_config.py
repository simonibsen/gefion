from pathlib import Path

from gefion.config import Settings, _parse_env_file, apply_dotenv, load_settings


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


# --- #158: host env identity + capability thresholds -----------------------

def test_load_settings_reads_gefion_env() -> None:
    s = load_settings(env={"GEFION_ENV": "production"}, include_os_env=False)
    assert s.gefion_env == "production"


def test_load_settings_defaults_env_to_dev() -> None:
    """Unknown host fails conservative — dev, never an unbounded posture."""
    s = load_settings(env={}, include_os_env=False)
    assert s.gefion_env == "dev"


def test_load_settings_invalid_env_falls_back_to_dev() -> None:
    s = load_settings(env={"GEFION_ENV": "staging"}, include_os_env=False)
    assert s.gefion_env == "dev"


def test_load_settings_reads_disk_threshold() -> None:
    s = load_settings(env={"GEFION_MIN_FREE_DISK_GB": "10"}, include_os_env=False)
    assert s.min_free_disk_gb == 10.0


def test_load_settings_bad_threshold_falls_back_to_default() -> None:
    s = load_settings(env={"GEFION_MIN_FREE_DISK_GB": "lots"}, include_os_env=False)
    assert s.min_free_disk_gb == 20.0


# --- #158: .env becomes the single SOT for the server ----------------------

def test_apply_dotenv_sets_missing_keys(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("GEFION_ENV=production\n")
    environ: dict[str, str] = {}

    applied = apply_dotenv(env_file=env_file, environ=environ)

    assert environ["GEFION_ENV"] == "production"
    assert applied["GEFION_ENV"] == "production"


def test_apply_dotenv_does_not_override_existing(tmp_path: Path) -> None:
    """The MCP client env block (already in os.environ) must win over .env."""
    env_file = tmp_path / ".env"
    env_file.write_text("GEFION_ENV=dev\n")
    environ = {"GEFION_ENV": "production"}

    apply_dotenv(env_file=env_file, environ=environ)

    assert environ["GEFION_ENV"] == "production"
