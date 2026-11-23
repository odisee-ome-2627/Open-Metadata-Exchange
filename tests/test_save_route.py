"""The contract of `POST /api/channel/{name}/save`."""

from collections.abc import Iterator
from http import HTTPStatus

import pytest
from fastapi.testclient import TestClient

from server.main import app, current_plugin
from server.plugins.ome_plugin import (
    EducationResource,
    OMEPlugin,
    SavedItem,
    can_save,
)
from server.schemas import SaveResponse

SAVE_URL = "/api/channel/ome.dspace/save"
client = TestClient(app)


class SavingPlugin(OMEPlugin):
    def save_resource(self, resource: EducationResource, ome_id: str) -> SavedItem:
        return SavedItem(cms_id=ome_id, cms_url=resource.source_url)


@pytest.fixture
def plugin_that_cannot_save() -> Iterator[None]:
    app.dependency_overrides[current_plugin] = OMEPlugin
    yield
    app.dependency_overrides.clear()


def test_only_a_plugin_with_its_own_save_resource_can_save() -> None:
    assert not can_save(OMEPlugin())
    assert can_save(SavingPlugin())


def test_the_default_save_resource_is_not_implemented() -> None:
    with pytest.raises(NotImplementedError):
        OMEPlugin().save_resource(EducationResource(), "<a@node>")


@pytest.mark.usefixtures("plugin_that_cannot_save")
@pytest.mark.thread_unsafe(reason="dependency_overrides belongs to the one app")
def test_a_plugin_that_cannot_save_gives_501() -> None:
    response = client.post(SAVE_URL, json={"id": 1})
    assert response.status_code == HTTPStatus.NOT_IMPLEMENTED
    assert response.json() == {"detail": "This plugin cannot save cards"}


@pytest.mark.parametrize("body", [None, {}, {"id": "seven"}])
def test_a_card_without_a_number_gives_422(body: dict | None) -> None:
    assert (
        client.post(SAVE_URL, json=body).status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    )


def test_the_contract_is_in_the_openapi_docs() -> None:
    operation = client.get("/openapi.json").json()["paths"]["/api/channel/{name}/save"][
        "post"
    ]
    assert [p["name"] for p in operation["parameters"]] == ["name"]
    assert set(operation["responses"]) == {"200", "404", "409", "422", "501", "502"}


def test_every_documented_answer_has_an_example_for_the_frontend_mocks() -> None:
    operation = client.get("/openapi.json").json()["paths"]["/api/channel/{name}/save"][
        "post"
    ]
    for status in ("404", "409", "422", "501", "502"):
        assert (
            "example" in operation["responses"][status]["content"]["application/json"]
        )
    example = SaveResponse.model_json_schema()["examples"][0]
    assert SaveResponse(**example).status == "saved"


def test_409_gives_the_existing_item_so_the_button_can_link_to_it() -> None:
    operation = client.get("/openapi.json").json()["paths"]["/api/channel/{name}/save"][
        "post"
    ]
    detail = operation["responses"]["409"]["content"]["application/json"]["example"][
        "detail"
    ]
    assert set(detail) == {"cms_id", "cms_url"}
