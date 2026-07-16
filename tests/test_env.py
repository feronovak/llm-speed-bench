import os
import socket

import pytest

from llm_bench.env import load_env_file
from llm_bench.security import require_http_url


def test_loads_production_env_without_overwriting_existing_value(tmp_path, monkeypatch):
    path = tmp_path / ".env.production"
    path.write_text(
        'OPENAI_API_KEY="from-file"\n'
        "GEMINI_API_KEY='gemini-file'\n"
        "# ignored comment\n"
    )
    monkeypatch.setenv("OPENAI_API_KEY", "already-set")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    load_env_file(path)

    assert os.environ["OPENAI_API_KEY"] == "already-set"
    assert os.environ["GEMINI_API_KEY"] == "gemini-file"


def test_missing_env_file_is_allowed(tmp_path):
    load_env_file(tmp_path / ".env.production")


def test_load_env_file_strips_unquoted_inline_comments(tmp_path, monkeypatch):
    path = tmp_path / ".env.production"
    path.write_text('PLAIN_KEY=value # comment\nQUOTED_KEY="value # not comment"\n')
    monkeypatch.delenv("PLAIN_KEY", raising=False)
    monkeypatch.delenv("QUOTED_KEY", raising=False)

    load_env_file(path)

    assert os.environ["PLAIN_KEY"] == "value"
    assert os.environ["QUOTED_KEY"] == "value # not comment"


@pytest.mark.parametrize(
    "url", ["http://127.0.0.1/v1", "http://[::1]/v1", "http://169.254.169.254/"]
)
def test_require_http_url_rejects_private_ip_targets(url):
    with pytest.raises(ValueError, match="public host"):
        require_http_url(url)


def test_require_http_url_rejects_hostname_resolving_to_private_address(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [(socket.AF_INET, 0, 0, "", ("10.0.0.7", 443))],
    )

    with pytest.raises(ValueError, match="public host"):
        require_http_url("https://provider.example/v1")
