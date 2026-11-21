"""
env.j2 turns config.json into the backend's .env (docker-entrypoint.sh runs j2).

These tests render it with Jinja2, the engine j2 uses, so no Docker is needed.
"""

from pathlib import Path

import pytest
from jinja2 import Environment, StrictUndefined

ENV_TEMPLATE = Path(__file__).resolve().parent.parent / "env.j2"

CONFIG = {
    "cms_plugin": "server.plugins.dspace.plugin.DSpacePlugin",
    "nntp_user": {"username": "someusername", "password": "somepassword"},
}


def render(config: dict) -> dict[str, str]:
    """Render env.j2 like j2 does and return the .env lines as a dict."""
    # j2 offers env() to read the container's environment variables.
    environment = Environment(undefined=StrictUndefined, autoescape=False)  # noqa: S701
    environment.globals["env"] = lambda name: f"<{name}>"
    text = environment.from_string(ENV_TEMPLATE.read_text()).render(**config)
    return dict(line.split("=", 1) for line in text.splitlines() if line)


def test_dspace_settings_come_from_config() -> None:
    env = render(
        CONFIG
        | {
            "dspace": {
                "oai_url": "http://dspace:8080/server/oai/request",
                "collection_handle": "123456789/2",
            }
        }
    )
    assert env["DSPACE_OAI_URL"] == "http://dspace:8080/server/oai/request"
    assert env["DSPACE_COLLECTION_HANDLE"] == "123456789/2"
    assert env["CMS_PLUGIN"] == "server.plugins.dspace.plugin.DSpacePlugin"


@pytest.mark.parametrize("dspace", [None, {}])
def test_a_node_without_dspace_settings_still_renders(dspace: dict | None) -> None:
    """No dspace block (or an empty one) must not stop the container."""
    config = CONFIG if dspace is None else CONFIG | {"dspace": dspace}
    env = render(config)
    assert env["DSPACE_OAI_URL"] == ""
    assert env["DSPACE_COLLECTION_HANDLE"] == ""
    assert env["NNTP_USER"] == "someusername"
