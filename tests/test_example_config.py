import re
import tomllib
from pathlib import Path

from igft.config import SETTING_NAMES, load_settings

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / "config.example.toml"
README = ROOT / "README.md"


def example_keys() -> set[str]:
    """Every setting in the example, including commented-out ones."""
    return set(re.findall(r"^#?\s*([a-z_]+)\s*=", EXAMPLE.read_text(), flags=re.MULTILINE))


def test_example_loads_through_the_config_loader():
    settings = load_settings(EXAMPLE, env={})
    assert settings.database_url.startswith("postgresql")


def test_example_mentions_every_setting():
    assert example_keys() == set(SETTING_NAMES)


def test_every_example_key_is_documented_in_readme():
    readme = README.read_text()
    missing = [key for key in example_keys() if f"`{key}`" not in readme]
    assert not missing


def test_example_contains_no_secrets():
    data = tomllib.loads(EXAMPLE.read_text())
    assert not any(word in key for key in data for word in ("password", "secret", "token", "cookie"))
    assert ":" not in data["database_url"].split("@")[0].removeprefix("postgresql+psycopg://")
