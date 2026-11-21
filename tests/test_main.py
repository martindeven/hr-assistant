"""Pruebas de la página que sirve ``app.main``.

La CSP de la spec 05 no permite código en línea: la lógica de la página vive en
``/static/app.js``, así que las comprobaciones de comportamiento leen ese archivo
y las de estructura leen ``index.html``.
"""

from pathlib import Path

STATIC_DIR = Path(__file__).resolve().parents[1] / "app" / "static"
INDEX_HTML = STATIC_DIR / "index.html"
APP_JS = STATIC_DIR / "app.js"


def test_home_page_paints_answers_with_text_content_and_never_inner_html():
    page = INDEX_HTML.read_text(encoding="utf-8")
    script = APP_JS.read_text(encoding="utf-8")

    assert "textContent" in script
    assert "innerHTML" not in script
    assert "innerHTML" not in page


def test_home_page_asks_for_the_key_and_keeps_it_only_in_session_storage(
    make_observed_client, api_key
):
    observed = make_observed_client(send_key=False)

    page = observed.http.get("/").text
    script_response = observed.http.get("/static/app.js")
    script = script_response.text

    assert 'data-testid="api-key"' in page
    assert 'type="password"' in page
    assert 'src="/static/app.js"' in page
    assert script_response.status_code == 200
    assert "sessionStorage" in script
    assert "X-API-Key" in script
    assert "localStorage" not in page + script
    assert api_key not in page + script
