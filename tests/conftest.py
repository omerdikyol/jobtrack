import pytest


@pytest.fixture(autouse=True)
def isolate_settings(tmp_path, monkeypatch):
    """Never read or write the real ~/settings.json during tests.

    Without this, a machine that has saved a provider or API key through the
    settings UI would change what the tests see.
    """
    from jobtrack import settings

    monkeypatch.setattr(settings, "SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.setattr(settings, "ENV_PATH", tmp_path / ".env")
