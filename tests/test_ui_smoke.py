"""
Smoke tests for the Streamlit UI driven by Playwright.

Marked with @pytest.mark.ui so the default test run stays headless-only.
Run with: pytest -m ui

Scope: smoke-tier only. These tests verify each page renders without
exceptions or JS errors — they do not exercise widget interactions,
form submission, or backend data correctness. Streamlit's WebSocket-
driven reruns make interaction-level tests inherently fragile, and
broad proactive coverage tends to produce flapping CI noise rather
than catch real bugs. Add a deeper test only when a specific user
flow has broken in a way this layer missed — then point the new test
exactly at that regression. Don't add interaction tests speculatively.
"""
import pytest

pytestmark = pytest.mark.ui


PAGES = [
    "Dashboard",
    "System Operations",
    "Data Management",
    "Features",
    "ML Pipeline",
    "Backtesting",
    "Experiments",
    "Charts",
    "Documentation",
    "Settings",
]


def _attach_error_listener(page):
    errors: list[str] = []
    page.on("pageerror", lambda exc: errors.append(f"pageerror: {exc}"))
    page.on(
        "console",
        lambda msg: errors.append(f"console.error: {msg.text}") if msg.type == "error" else None,
    )
    return errors


def _assert_clean(page, errors):
    assert page.locator('[data-testid="stException"]').count() == 0, (
        "Streamlit rendered an exception banner"
    )
    assert not errors, f"JS errors: {errors}"


def _open_app(page, base_url: str):
    page.goto(base_url, wait_until="load")
    page.locator('[data-testid="stSidebar"]').wait_for(state="visible", timeout=30_000)


def test_home_page_loads(streamlit_server, page):
    """UI boots and the home (Dashboard) page renders cleanly."""
    errors = _attach_error_listener(page)
    _open_app(page, streamlit_server)
    _assert_clean(page, errors)
    assert len(page.inner_text("body")) > 0


def test_sidebar_contains_all_pages(streamlit_server, page):
    """The sidebar lists every page from the navigation."""
    _open_app(page, streamlit_server)
    sidebar = page.locator('[data-testid="stSidebar"]')
    missing = [
        label
        for label in PAGES
        if sidebar.get_by_role("button", name=label).count() == 0
    ]
    assert not missing, f"Sidebar is missing buttons for: {missing}"


@pytest.mark.parametrize("page_name", PAGES)
def test_each_page_renders(streamlit_server, page, page_name):
    """Each sidebar page renders without exceptions when navigated to."""
    errors = _attach_error_listener(page)
    _open_app(page, streamlit_server)
    page.locator('[data-testid="stSidebar"]').get_by_role(
        "button", name=page_name
    ).first.click()
    page.wait_for_timeout(1500)  # let Streamlit rerun
    _assert_clean(page, errors)
