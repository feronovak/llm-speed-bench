import os
import socket
import urllib.error
import urllib.request

import pytest

from llm_preflight.env import load_env_file
from llm_preflight.security import NoRedirectHandler, require_http_url


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


def test_load_env_file_strips_the_export_keyword(tmp_path, monkeypatch):
    path = tmp_path / ".env.production"
    path.write_text("export EXPORTED_KEY=value\n")
    monkeypatch.delenv("EXPORTED_KEY", raising=False)

    load_env_file(path)

    assert os.environ["EXPORTED_KEY"] == "value"


def test_load_env_file_rejects_a_line_without_an_equals_sign(tmp_path):
    path = tmp_path / ".env.production"
    path.write_text("NOT_AN_ASSIGNMENT\n")

    with pytest.raises(ValueError, match="expected KEY=value"):
        load_env_file(path)


def test_load_env_file_rejects_an_invalid_variable_name(tmp_path):
    path = tmp_path / ".env.production"
    path.write_text("1INVALID=value\n")

    with pytest.raises(ValueError, match="invalid variable name '1INVALID'"):
        load_env_file(path)


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


def test_invalid_scheme_does_not_echo_embedded_credentials():
    with pytest.raises(ValueError, match="embedded credentials") as exc_info:
        require_http_url("file://user:super-secret@example.com/path")

    assert "super-secret" not in str(exc_info.value)


def test_require_http_url_rejects_hostname_resolving_to_private_address(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [(socket.AF_INET, 0, 0, "", ("10.0.0.7", 443))],
    )

    with pytest.raises(ValueError, match="public host"):
        require_http_url("https://provider.example/v1")


def test_provider_requests_refuse_redirects_instead_of_following_to_another_host():
    handler = NoRedirectHandler()
    request = urllib.request.Request("https://provider.example/v1")

    with pytest.raises(urllib.error.HTTPError, match="redirects are not allowed"):
        handler.redirect_request(request, None, 302, "Found", {}, "http://127.0.0.1/")
