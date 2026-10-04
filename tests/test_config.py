from datetime import timedelta
from pathlib import Path

import platformdirs.unix
import pytest

from igft import config
from igft.config import ConfigError, load_settings


@pytest.fixture
def ubuntu_home(monkeypatch, tmp_path):
    """Resolve platform directories as on Ubuntu, whatever OS runs the tests."""
    home = tmp_path / "home" / "ivan"
    home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    for var in ("XDG_DATA_HOME", "XDG_CONFIG_HOME"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(config, "_dirs", lambda: platformdirs.unix.Unix(config.APP_NAME, appauthor=False))
    return home


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


# --- 3.3 defaults -------------------------------------------------------------


def test_defaults(ubuntu_home):
    s = load_settings(env={})
    assert (s.delay_min_seconds, s.delay_max_seconds) == (15.0, 45.0)
    assert s.page_cap == 300
    assert s.early_stop_pages == 2
    assert s.cooldown == timedelta(hours=24)
    assert s.database_url is None and s.firefox_profile is None and s.instaloader_user_agent is None


def test_ubuntu_session_paths(ubuntu_home):
    s = load_settings(env={})
    sessions = ubuntu_home / ".local/share/igft/sessions"
    assert s.instaloader_session_path == sessions / "instaloader.session"


def test_session_paths_respect_xdg_data_home(ubuntu_home, monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    s = load_settings(env={})
    assert s.instaloader_session_path == tmp_path / "xdg-data/igft/sessions/instaloader.session"


def test_ubuntu_default_config_path_respects_xdg_config_home(ubuntu_home, monkeypatch, tmp_path):
    assert config.default_config_path() == ubuntu_home / ".config/igft/config.toml"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    assert config.default_config_path() == tmp_path / "xdg-config/igft/config.toml"


def test_settings_are_frozen(ubuntu_home):
    s = load_settings(env={})
    with pytest.raises(AttributeError):
        s.page_cap = 1


# --- 3.4 loading precedence --------------------------------------------------------


def test_missing_default_file_falls_back_to_defaults(ubuntu_home):
    assert not config.default_config_path().exists()
    assert load_settings(env={}).page_cap == 300


def test_default_file_is_read(ubuntu_home):
    write(config.default_config_path(), "page_cap = 10\n")
    assert load_settings(env={}).page_cap == 10


def test_igft_config_env_overrides_default_file(ubuntu_home, tmp_path):
    write(config.default_config_path(), "page_cap = 10\n")
    other = write(tmp_path / "other.toml", "page_cap = 20\n")
    assert load_settings(env={"IGFT_CONFIG": str(other)}).page_cap == 20


def test_explicit_path_overrides_igft_config(ubuntu_home, tmp_path):
    from_env = write(tmp_path / "env.toml", "page_cap = 20\n")
    explicit = write(tmp_path / "explicit.toml", "page_cap = 30\n")
    assert load_settings(explicit, env={"IGFT_CONFIG": str(from_env)}).page_cap == 30


def test_explicit_or_env_file_must_exist(ubuntu_home, tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_settings(tmp_path / "missing.toml", env={})
    with pytest.raises(ConfigError, match="not found"):
        load_settings(env={"IGFT_CONFIG": str(tmp_path / "missing.toml")})


def test_env_variables_override_the_file(ubuntu_home, tmp_path):
    path = write(tmp_path / "c.toml", 'database_url = "postgresql://file"\npage_cap = 10\n')
    s = load_settings(
        path,
        env={
            "IGFT_DATABASE_URL": "postgresql://env",
            "IGFT_PAGE_CAP": "5",
            "IGFT_DELAY_MIN_SECONDS": "1.5",
            "IGFT_FIREFOX_PROFILE": "~/ff",
        },
    )
    assert s.database_url == "postgresql://env"
    assert s.page_cap == 5
    assert s.delay_min_seconds == 1.5
    assert s.firefox_profile == ubuntu_home / "ff"


def test_unrelated_igft_env_variables_are_ignored(ubuntu_home):
    s = load_settings(env={"IGFT_TEST_DATABASE_URL": "postgresql://test"})
    assert s.database_url is None


def test_paths_in_file_expand_home(ubuntu_home, tmp_path):
    path = write(tmp_path / "c.toml", 'instaloader_session_path = "~/s/il.session"\n')
    assert load_settings(path, env={}).instaloader_session_path == ubuntu_home / "s/il.session"


def test_unknown_key_in_file_is_rejected(ubuntu_home, tmp_path):
    path = write(tmp_path / "c.toml", "page_capp = 10\n")
    with pytest.raises(ConfigError, match="page_capp"):
        load_settings(path, env={})


def test_invalid_toml_is_rejected(ubuntu_home, tmp_path):
    path = write(tmp_path / "c.toml", "page_cap = = 1\n")
    with pytest.raises(ConfigError, match="not valid TOML"):
        load_settings(path, env={})


# --- 3.5 validation ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({"IGFT_DELAY_MIN_SECONDS": "50", "IGFT_DELAY_MAX_SECONDS": "40"}, "must not be greater"),
        ({"IGFT_DELAY_MIN_SECONDS": "-1"}, "must not be negative"),
        ({"IGFT_DELAY_MIN_SECONDS": "-5", "IGFT_DELAY_MAX_SECONDS": "-1"}, "must not be negative"),
        ({"IGFT_PAGE_CAP": "0"}, "page_cap must be at least 1"),
        ({"IGFT_EARLY_STOP_PAGES": "0"}, "early_stop_pages must be at least 1"),
        ({"IGFT_COOLDOWN_HOURS": "0"}, "cooldown_hours must be greater than 0"),
        ({"IGFT_PAGE_CAP": "lots"}, "page_cap"),
    ],
)
def test_invalid_values_raise_config_error(ubuntu_home, env, expected):
    with pytest.raises(ConfigError, match=expected):
        load_settings(env=env)


def test_wrong_type_in_file_is_reported(ubuntu_home, tmp_path):
    path = write(tmp_path / "c.toml", "page_cap = 2.5\nearly_stop_pages = true\n")
    with pytest.raises(ConfigError) as info:
        load_settings(path, env={})
    assert "page_cap" in info.value.message and "early_stop_pages" in info.value.message
