"""D3 chart template engine — load templates, serialize data, render HTML."""
import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, Optional

import jinja2

from gefion.charts.d3.theme import get_css
from gefion.observability import create_span, set_attributes

TEMPLATES_DIR = Path(__file__).parent / "templates"
VENDOR_DIR = Path(__file__).parent / "vendor"

_env = jinja2.Environment(
    loader=jinja2.FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=False,
)


def load_template(template_name: str) -> jinja2.Template:
    """Load a Jinja2 template from the templates directory."""
    return _env.get_template(template_name)


class _D3JSONEncoder(json.JSONEncoder):
    """JSON encoder that handles date, Decimal, and other Python types."""

    def default(self, obj):
        if isinstance(obj, (date, datetime)):
            return obj.isoformat()
        if isinstance(obj, Decimal):
            return float(obj)
        return super().default(obj)


def serialize_for_d3(data: Any) -> str:
    """Serialize Python data to JSON string safe for HTML embedding.

    Handles date objects, Decimal, None. Escapes </script> to prevent XSS.
    """
    raw = json.dumps(data, cls=_D3JSONEncoder)
    # Escape </script> to prevent breaking out of <script> blocks
    return raw.replace("</script>", "<\\/script>")


def _get_d3_script_tag() -> str:
    """Return script tag for D3.js — CDN with vendored fallback."""
    vendor_path = VENDOR_DIR / "d3.v7.min.js"
    if vendor_path.exists():
        d3_src = vendor_path.read_text()
        return (
            '<script src="https://cdn.jsdelivr.net/npm/d3@7/dist/d3.min.js"></script>\n'
            f'<script>if(typeof d3==="undefined"){{{d3_src}}}</script>'
        )
    return '<script src="https://cdn.jsdelivr.net/npm/d3@7/dist/d3.min.js"></script>'


def render_d3_chart(
    template_name: str,
    data: Dict[str, Any],
    config: Optional[Dict[str, Any]] = None,
    width: int = 800,
    height: int = 600,
) -> str:
    """Render a D3 chart as self-contained HTML string.

    Args:
        template_name: Name of Jinja2 template in templates/
        data: Chart data dict (serialized to JSON and injected)
        config: Optional chart config (colors, labels, etc.)
        width: Chart width in pixels
        height: Chart height in pixels

    Returns:
        Self-contained HTML string with embedded D3.js and data.
    """
    with create_span("charts.d3.render", template=template_name, width=width, height=height) as span:
        template = load_template(template_name)
        html = template.render(
            data_json=serialize_for_d3(data),
            config_json=serialize_for_d3(config or {}),
            width=width,
            height=height,
            theme_css=get_css(),
            d3_script=_get_d3_script_tag(),
        )
        set_attributes(span, html_length=len(html))
        return html
