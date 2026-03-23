import os
from typing import Optional

from playwright.sync_api import Error as PlaywrightError


MUTE_INIT_SCRIPT = """
(() => {
    const muteMedia = () => {
        for (const el of document.querySelectorAll('audio,video')) {
            try {
                el.muted = true;
                el.volume = 0;
            } catch (_) {}
        }
    };
    const observer = new MutationObserver(muteMedia);
    observer.observe(document.documentElement || document, { childList: true, subtree: true });
    muteMedia();

    const AC = window.AudioContext || window.webkitAudioContext;
    if (AC && AC.prototype) {
        const originalResume = AC.prototype.resume;
        AC.prototype.resume = function() {
            try { return this.suspend(); } catch (_) {}
            return Promise.resolve();
        };
        if (typeof originalResume === 'function') {
            AC.prototype._bridgeOriginalResume = originalResume;
        }
    }
})();
"""


def _apply_audio_mute_hooks(context, page) -> None:
        """Best-effort page-level audio muting for dynamic media sources."""
        try:
                context.add_init_script(MUTE_INIT_SCRIPT)
                page.evaluate(
                        """
                        () => {
                            for (const el of document.querySelectorAll('audio,video')) {
                                try {
                                    el.muted = true;
                                    el.volume = 0;
                                } catch (_) {}
                            }
                        }
                        """
                )
        except PlaywrightError:
                pass


def launch_browser_session(
    playwright,
    *,
    browser_name: str,
    headless: bool,
    mute_audio: bool,
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
            chromium_args = [f"--window-size={window_width},{window_height}"]
            if mute_audio:
                chromium_args.append("--mute-audio")
            launch_kwargs["args"] = chromium_args
        elif mute_audio:
            launch_kwargs["firefox_user_prefs"] = {
                "media.volume_scale": "0.0",
            }

        try:
            context = browser_type.launch_persistent_context(profile_dir, **launch_kwargs)
            page = context.pages[0] if context.pages else context.new_page()
            if mute_audio:
                _apply_audio_mute_hooks(context, page)
            return context, page, browser
        except PlaywrightError as exc:
            # Common case: profile already locked by another browser process.
            print(f"[bridge] Persistent profile launch failed: {exc}")
            print("[bridge] Falling back to non-persistent browser context")

    launch_kwargs = {"headless": headless}
    if browser_name == "chromium":
        chromium_args = [f"--window-size={window_width},{window_height}"]
        if mute_audio:
            chromium_args.append("--mute-audio")
        launch_kwargs["args"] = chromium_args
    elif mute_audio:
        launch_kwargs["firefox_user_prefs"] = {
            "media.volume_scale": "0.0",
        }

    browser = browser_type.launch(**launch_kwargs)
    context = browser.new_context(viewport={"width": window_width, "height": window_height})
    page = context.new_page()
    if mute_audio:
        _apply_audio_mute_hooks(context, page)
    return context, page, browser
