from fastapi.testclient import TestClient

from main import app


def test_render_root_redirects_to_public_loader() -> None:
    client = TestClient(app, base_url="https://compass-knou-cs-ai-navigator.onrender.com")

    response = client.get("/", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"] == "https://mhjang-qa.github.io/ComPass/"


def test_github_loader_iframe_can_open_render_app() -> None:
    client = TestClient(app, base_url="https://compass-knou-cs-ai-navigator.onrender.com")

    response = client.get("/?from=github-pages", follow_redirects=False)

    assert response.status_code == 200
    assert "ComPass" in response.text
