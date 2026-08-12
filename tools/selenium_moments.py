"""Headless-browser test of the NEW Moments screen + navbar injection.

Asserts, in a real Chromium:
  1. /moment/ (no params) loads the latest match: video loads, playlist populated
  2. clicking a playlist item seeks the video and shows its coach note
  3. Next/Prev buttons advance the counter and re-seek
  4. every page's navbar has a working "Moments" link
  5. page is NOT blurred / click-blocked (the old mockup bug)

    python -m tools.selenium_check-style:  python -m tools.selenium_moments [base] [hub]
"""
from __future__ import annotations

import json
import sys
import time

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By


def video_info(d, v):
    return d.execute_script(
        "var v=arguments[0];return {rs:v.readyState,ct:v.currentTime,"
        "dur:v.duration,err:(v.error&&v.error.code)||null};", v)


def main() -> None:
    base = sys.argv[1] if len(sys.argv) > 1 else "http://api:8000"
    hub = sys.argv[2] if len(sys.argv) > 2 else "http://cx-selenium:4444"
    opts = Options()
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
        # --- 1) moments screen, no params -> latest match ---------------------
        d.get(base + "/moment/")
        time.sleep(4)
        items = d.find_elements(By.CSS_SELECTOR, ".cx-mom")
        res["playlist_items"] = len(items)
        vids = d.find_elements(By.TAG_NAME, "video")
        res["video_present"] = bool(vids)
        if vids:
            for _ in range(15):
                if video_info(d, vids[0])["rs"] >= 1:
                    break
                time.sleep(1)
            res["video"] = video_info(d, vids[0])
            res["video_loaded"] = res["video"]["rs"] >= 1 and not res["video"]["err"]

        # main must not be blurred or click-blocked (the old mockup bug)
        style = d.execute_script(
            "var m=document.querySelector('main');var cs=getComputedStyle(m);"
            "return {filter:cs.filter, pe:cs.pointerEvents};")
        res["not_blurred"] = style["filter"] in ("none", "") and style["pe"] != "none"

        # --- 2) click the 2nd playlist item -> seek + note --------------------
        if len(items) >= 2:
            items[1].click()
            time.sleep(2)
            info = video_info(d, vids[0])
            res["click_seeked_ct"] = info["ct"]
            res["click_seeks"] = info["ct"] > 0
            note = d.find_element(By.ID, "cx-note").text
            res["note_after_click"] = bool(note.strip())
        else:
            res["click_seeks"] = res["note_after_click"] = len(items) == 0  # vacuous ok if no data

        # --- 3) next / prev ---------------------------------------------------
        if items:
            before = d.find_element(By.ID, "cx-count").text
            d.find_element(By.ID, "cx-next").click()
            time.sleep(1)
            after = d.find_element(By.ID, "cx-count").text
            res["next_advances"] = before != after
            d.find_element(By.ID, "cx-prev").click()
            time.sleep(1)
            res["prev_returns"] = d.find_element(By.ID, "cx-count").text == before
        else:
            res["next_advances"] = res["prev_returns"] = True

        # --- 4) navbar Moments link on other pages ----------------------------
        nav_ok = {}
        for page in ("/", "/upload/", "/report/", "/trends/"):
            d.get(base + page)
            time.sleep(2)
            nav_ok[page] = bool(d.find_elements(By.CSS_SELECTOR, 'nav a[href="/moment/"]'))
        res["nav_moments_links"] = nav_ok
        res["nav_all_pages"] = all(nav_ok.values())

        checks = {
            "video_loaded": res.get("video_loaded"),
            "playlist_populated": res.get("playlist_items", 0) > 0,
            "not_blurred": res.get("not_blurred"),
            "click_seeks": res.get("click_seeks"),
            "note_after_click": res.get("note_after_click"),
            "next_advances": res.get("next_advances"),
            "prev_returns": res.get("prev_returns"),
            "nav_all_pages": res.get("nav_all_pages"),
        }
        res["checks"] = checks
        res["ALL_PASS"] = all(bool(v) for v in checks.values())
    finally:
        d.quit()
    print("SELENIUM_RESULT:", json.dumps(res, indent=2, default=str))


if __name__ == "__main__":
    main()
