from http import HTTPStatus

import pytest
from fastapi.testclient import TestClient

from server.main import app

SAVE_URL = "/api/channel/ome.eric/save"


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_save_is_not_implemented_yet(client: TestClient) -> None:
    response = client.post(SAVE_URL, json={"id": 1})
    assert response.status_code == HTTPStatus.NOT_IMPLEMENTED
    assert response.json() == {"detail": "This plugin cannot save cards"}


@pytest.mark.parametrize("body", [None, {}, {"id": "not-a-number"}])
def test_save_rejects_an_invalid_card(client: TestClient, body: dict | None) -> None:
    response = client.post(SAVE_URL, json=body)
    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


def test_save_route_documents_its_contract(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]
    operation = paths["/api/channel/{name}/save"]["post"]
    assert [param["name"] for param in operation["parameters"]] == ["name"]
    assert operation["requestBody"]["required"] is True
    assert operation["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/SaveResponse"
    }
