"""Pruebas de la página que sirve ``app.main``."""

from pathlib import Path

INDEX_HTML = Path(__file__).resolve().parents[1] / "app" / "static" / "index.html"


def test_home_page_paints_answers_with_text_content_and_never_inner_html():
    page = INDEX_HTML.read_text(encoding="utf-8")

    assert "textContent" in page
    assert "innerHTML" not in page


def test_home_page_asks_for_the_key_and_keeps_it_only_in_session_storage(
    make_observed_client, api_key
):
    observed = make_observed_client(send_key=False)

    page = observed.http.get("/").text

    assert 'data-testid="api-key"' in page
    assert 'type="password"' in page
    assert "sessionStorage" in page
    assert "X-API-Key" in page
    assert "localStorage" not in page
    assert api_key not in page
