import importlib

import pytest
from django.core.exceptions import ImproperlyConfigured

from config import settings as project_settings


@pytest.fixture
def reload_settings(monkeypatch):
    def reload(**variables):
        for name, value in variables.items():
            monkeypatch.setenv(name, value)
        return importlib.reload(project_settings)

    yield reload
    monkeypatch.undo()
    importlib.reload(project_settings)


def addressing_style(module) -> str:
    return module.STORAGES["submissions"]["OPTIONS"]["addressing_style"]


def test_defaults_to_path_style_for_the_local_storage(monkeypatch, reload_settings):
    monkeypatch.delenv("AWS_S3_ADDRESSING_STYLE", raising=False)

    assert addressing_style(reload_settings()) == "path"


@pytest.mark.parametrize("style", ["path", "virtual", "auto"])
def test_uses_the_configured_addressing_style(reload_settings, style):
    assert addressing_style(reload_settings(AWS_S3_ADDRESSING_STYLE=style)) == style


def test_rejects_an_unknown_addressing_style(reload_settings):
    with pytest.raises(ImproperlyConfigured, match="AWS_S3_ADDRESSING_STYLE"):
        reload_settings(AWS_S3_ADDRESSING_STYLE="virtual-host")
