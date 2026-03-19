import os
from typing import Optional

from playwright.sync_api import Error as PlaywrightError


def launch_browser_session(
    playwright,
    *,
    browser_name: str,
    headless: bool,
    window_width: int,
    window_height: int,
    persistent_profile: bool,
    user_data_dir: str,
):
    """Create a Playwright context/page session for the bridge runtime."""
    browser = None
    browser_type = playwright.chromium if browser_name == "chromium" else playwright.firefox

    if persistent_profile:
        profile_dir = os.path.abspath(user_data_dir)
        os.makedirs(profile_dir, exist_ok=True)
        launch_kwargs = {
            "headless": headless,
            "viewport": {"width": window_width, "height": window_height},
        }
        if browser_name == "chromium":
            launch_kwargs["args"] = [f"--window-size={window_width},{window_height}"]

        try:
            context = browser_type.launch_persistent_context(profile_dir, **launch_kwargs)
            page = context.pages[0] if context.pages else context.new_page()
            return context, page, browser
        except PlaywrightError as exc:
            # Common case: profile already locked by another browser process.
            print(f"[bridge] Persistent profile launch failed: {exc}")
            print("[bridge] Falling back to non-persistent browser context")

    launch_kwargs = {"headless": headless}
    if browser_name == "chromium":
        launch_kwargs["args"] = [f"--window-size={window_width},{window_height}"]

    browser = browser_type.launch(**launch_kwargs)
    context = browser.new_context(viewport={"width": window_width, "height": window_height})
    page = context.new_page()
    return context, page, browser
