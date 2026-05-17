"""
Smoke tests for the Streamlit UI driven by Playwright.

Marked with @pytest.mark.ui so the default test run stays headless-only.
Run with: pytest -m ui
"""
import pytest

pytestmark = pytest.mark.ui


def test_home_page_loads(streamlit_server, page):
    """UI boots and the home page renders without JS errors or Streamlit exception banners."""
    errors: list[str] = []
    page.on("pageerror", lambda exc: errors.append(f"pageerror: {exc}"))
    page.on(
        "console",
        lambda msg: errors.append(f"console.error: {msg.text}") if msg.type == "error" else None,
    )

    page.goto(streamlit_server, wait_until="networkidle")

    assert page.locator('[data-testid="stException"]').count() == 0, (
        "Streamlit rendered an exception banner on the home page"
    )

    body_text = page.inner_text("body")
    assert len(body_text) > 0, "Home page body is empty"

    assert not errors, f"JS errors during page load: {errors}"
