"""
env.j2 turns config.json into the backend's .env (docker-entrypoint.sh runs j2).

These tests render it with Jinja2, the engine j2 uses, so no Docker is needed.
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from jinja2 import Environment, StrictUndefined

ENV_TEMPLATE = Path(__file__).resolve().parent.parent / "env.j2"

CONFIG = {
    "cms_plugin": "server.plugins.dspace.plugin.DSpacePlugin",
    "nntp_user": {"username": "someusername", "password": "somepassword"},
}
# Values that broke the unquoted .env: a space, and the characters that need escaping
AWKWARD = {
    "site_name": "Example Library",
    "logo": "https://example.org/logo.png?name=two words",
    "librarian_contact": "<address we agree on>",
}
ESCAPED = r"""a $HOME "quoted" back\slash 's"""


def render_text(config: dict) -> str:
    """Render env.j2 like j2 does."""
    # j2 offers env() to read the container's environment variables.
    environment = Environment(undefined=StrictUndefined, autoescape=False)  # noqa: S701
    environment.globals["env"] = lambda name: f"<{name}>"
    return environment.from_string(ENV_TEMPLATE.read_text()).render(**config)


def unquote(value: str) -> str:
    """Read a double-quoted .env value, with the escapes env.j2 writes."""
    assert value.startswith('"'), value
    assert value.endswith('"'), value
    chars = iter(value[1:-1])
    return "".join(next(chars) if char == "\\" else char for char in chars)


def render(config: dict) -> dict[str, str]:
    """Render env.j2 and return the .env lines as a dict of unquoted values."""
    lines = (line.split("=", 1) for line in render_text(config).splitlines() if line)
    return {name: unquote(value) for name, value in lines}


def test_dspace_settings_come_from_config() -> None:
    env = render(
        CONFIG
        | {
            "dspace": {
                "oai_url": "http://dspace:8080/server/oai/request",
                "handle_prefix": "123456789",
                "collection_handle": "123456789/2",
                "newsgroup": "ome.odisee",
                "site_name": "Odisee",
                "logo": "https://example.org/logo.png?itok=abc",
                "librarian_contact": "librarian@example.org",
            }
        }
    )
    assert env["DSPACE_OAI_URL"] == "http://dspace:8080/server/oai/request"
    assert env["DSPACE_HANDLE_PREFIX"] == "123456789"
    assert env["DSPACE_COLLECTION_HANDLE"] == "123456789/2"
    assert env["DSPACE_NEWSGROUP"] == "ome.odisee"
    assert env["DSPACE_SITE_NAME"] == "Odisee"
    assert env["DSPACE_LOGO"] == "https://example.org/logo.png?itok=abc"
    assert env["DSPACE_LIBRARIAN_CONTACT"] == "librarian@example.org"
    assert env["CMS_PLUGIN"] == "server.plugins.dspace.plugin.DSpacePlugin"


@pytest.mark.parametrize("dspace", [None, {}])
def test_a_node_without_dspace_settings_still_renders(dspace: dict | None) -> None:
    """No dspace block (or an empty one) must not stop the container."""
    config = CONFIG if dspace is None else CONFIG | {"dspace": dspace}
    env = render(config)
    assert env["DSPACE_OAI_URL"] == ""
    assert env["DSPACE_HANDLE_PREFIX"] == ""
    assert env["DSPACE_COLLECTION_HANDLE"] == ""
    assert env["DSPACE_NEWSGROUP"] == ""
    assert env["DSPACE_SITE_NAME"] == ""
    assert env["DSPACE_LOGO"] == ""
    assert env["DSPACE_LIBRARIAN_CONTACT"] == ""
    assert env["NNTP_USER"] == "someusername"


def test_values_with_spaces_and_special_characters_are_quoted() -> None:
    config = CONFIG | {"nntp_user": {"username": "u", "password": ESCAPED}}
    env = render(config | {"dspace": AWKWARD})
    assert env["DSPACE_SITE_NAME"] == "Example Library"
    assert env["DSPACE_LOGO"] == AWKWARD["logo"]
    assert env["DSPACE_LIBRARIAN_CONTACT"] == AWKWARD["librarian_contact"]
    assert env["NNTP_PASSWD"] == ESCAPED


@pytest.mark.skipif(not shutil.which("uv"), reason="needs uv, which reads the .env")
def test_uv_reads_every_line_of_the_rendered_env(tmp_path: Path) -> None:
    """
    The backend starts with `uv run --env-file=.env`. With an unquoted value with a
    space, uv only warned "Failed to parse environment file" and loaded nothing.
    """
    config = CONFIG | {"nntp_user": {"username": "u", "password": ESCAPED}}
    env_file = tmp_path / ".env"
    env_file.write_text(render_text(config | {"dspace": AWKWARD}), encoding="utf-8")
    names = ["DSPACE_SITE_NAME", "DSPACE_LOGO", "DSPACE_LIBRARIAN_CONTACT"]
    names += ["NNTP_PASSWD", "CMS_PLUGIN"]
    code = f"import json, os; print(json.dumps([os.getenv(n) for n in {names!r}]))"
    uv = ["uv", "run", "--no-project", "--env-file", env_file.as_posix()]
    result = subprocess.run(  # noqa: S603
        [shutil.which("uv"), *uv[1:], sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "Failed to parse" not in result.stderr
    assert json.loads(result.stdout) == [
        "Example Library",
        AWKWARD["logo"],
        AWKWARD["librarian_contact"],
        ESCAPED,
        CONFIG["cms_plugin"],
    ]
