"""Headless browser smoke-test of the moment viewer (proves the real page works).

Drives a real Chromium (via a Selenium standalone container) against the running
app and asserts: the page renders, the video actually loads + seeks, the coaching
comment shows, and the buttons are present — with no severe console errors.

    python -m tools.selenium_check "<url>" [selenium_hub]
"""
from __future__ import annotations

import json
import sys
import time

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By


def main() -> None:
    url = sys.argv[1]
    hub = sys.argv[2] if len(sys.argv) > 2 else "http://cx-selenium:4444"
    opts = Options()
    opts.set_capability("goog:loggingPrefs", {"browser": "ALL"})
    # Wait for the Selenium hub to come up (container may still be starting).
    d = None
    for _ in range(30):
        try:
            d = webdriver.Remote(command_executor=hub, options=opts)
            break
        except Exception:
            time.sleep(2)
    if d is None:
        print("SELENIUM_RESULT:", json.dumps({"ALL_PASS": False, "error": "hub not reachable"}))
        return
    res: dict = {}
    try:
        d.get(url)
        time.sleep(3)
        res["page_title"] = d.title
        res["url_ok"] = "/moment/" in d.current_url

        vids = d.find_elements(By.TAG_NAME, "video")
        res["video_element_present"] = bool(vids)
        if vids:
            # let it buffer + let initMoment seek
            for _ in range(12):
                info = d.execute_script(
                    "var v=arguments[0];return {rs:v.readyState,ct:v.currentTime,"
                    "dur:v.duration,src:v.currentSrc,err:(v.error&&v.error.code)||null};", vids[0])
                if info["rs"] and info["rs"] >= 1:
                    break
                time.sleep(1)
            time.sleep(2)
            info = d.execute_script(
                "var v=arguments[0];return {rs:v.readyState,ct:v.currentTime,"
                "dur:v.duration,src:v.currentSrc,err:(v.error&&v.error.code)||null};", vids[0])
            res["video"] = info
            res["video_loaded"] = bool(info["rs"] and info["rs"] >= 1 and not info["err"])
            res["video_seeked"] = bool(info["ct"] and info["ct"] > 60)

        from urllib.parse import parse_qs, unquote, urlparse
        expected = (parse_qs(urlparse(url).query).get("c") or [""])[0]
        expected = unquote(expected)
        body = d.find_element(By.TAG_NAME, "body").text
        res["expected_comment"] = expected
        res["comment_shown"] = bool(expected) and expected in body
        res["coach_note_label"] = "COACH'S NOTE" in body.upper()

        btns = [b.text.strip() for b in d.find_elements(By.TAG_NAME, "button")]
        res["back_button"] = any("Back to report" in b for b in btns)

        try:
            logs = d.get_log("browser")
            res["severe_console_errors"] = [
                l["message"][:160] for l in logs if l.get("level") == "SEVERE"]
        except Exception:
            res["severe_console_errors"] = "n/a"

        sev = res.get("severe_console_errors")
        no_severe = (sev == "n/a") or (isinstance(sev, list) and not sev)
        checks = {
            "video_loaded": res.get("video_loaded"),
            "video_seeked": res.get("video_seeked"),
            "comment_shown": res.get("comment_shown"),
            "coach_note_label": res.get("coach_note_label"),
            "back_button": res.get("back_button"),
            "no_severe_errors": no_severe,
        }
        res["ALL_PASS"] = all(bool(v) for v in checks.values())
        res["checks"] = checks
    finally:
        d.quit()
    print("SELENIUM_RESULT:", json.dumps(res, indent=2, default=str))


if __name__ == "__main__":
    main()
