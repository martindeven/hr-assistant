"""Página del tablero y su política de contenido (reto, parte 2, punto 5)."""

import base64
import hashlib
import re
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "app" / "static"
INLINE_BLOCK = r"<{tag}(?:\s[^>]*)?>(.*?)</{tag}>"


def csp_hash(text):
    """El hash de CSP de un bloque en línea (``'sha256-...'``)."""
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    return f"'sha256-{base64.b64encode(digest).decode('ascii')}'"


def inline_blocks(html, tag):
    """El contenido de cada bloque ``<script>`` o ``<style>`` del HTML."""
    return re.findall(INLINE_BLOCK.format(tag=tag), html, flags=re.DOTALL)


def test_the_dashboard_inline_code_is_allowed_only_by_hash_and_avoids_inner_html(
    make_observed_client,
):
    html = (STATIC / "dashboard.html").read_text(encoding="utf-8")
    scripts = inline_blocks(html, "script")
    styles = inline_blocks(html, "style")

    csp = make_observed_client(send_key=False).http.get("/dashboard").headers[
        "Content-Security-Policy"
    ]

    assert len(scripts) == 1 and len(styles) == 1
    script_src = re.search(r"script-src ([^;]*)", csp).group(1)
    style_src = re.search(r"style-src ([^;]*)", csp).group(1)
    assert csp_hash(scripts[0]) in script_src.split()
    assert csp_hash(styles[0]) in style_src.split()
    assert "'unsafe-inline'" not in csp
    assert "innerHTML" not in html
    assert "sessionStorage" in html and "textContent" in html
    assert not re.search(r"""(?:src|href)=["']?(?:https?:)?//""", html)
    assert not re.search(r"\s(?:style|on[a-z]+)=", html)
