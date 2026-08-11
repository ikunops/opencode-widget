#!/usr/bin/env python3
"""pywebview Console 登录辅助。

打开 opencode.ai/auth 登录窗口，检测到登录完成（导航到 workspace 页面）后：
1. 抓取 auth cookie + workspace ID 写入 config.json
2. 触发 data_server 同步 (/api/sync)
"""
import json
import os
import re
import sys
import threading
import time

APP_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(APP_DIR, "config.json")


def load_config():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except Exception:
        return {}


def save_config(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def trigger_sync():
    try:
        import urllib.request
        req = urllib.request.Request("http://127.0.0.1:8765/api/sync", data=b"{}",
                                     headers={"Content-Type": "application/json"}, method="POST")
        urllib.request.urlopen(req, timeout=15)
    except Exception:
        pass


def main():
    import webview

    win = webview.create_window(
        "Go Console",
        "https://opencode.ai/auth",
        width=980,
        height=760,
        x=200,
        y=60,
        on_top=False,
        transparent=False,
        background_color="#ffffff",
        easy_drag=False,
    )

    def watcher():
        for _ in range(900):  # 最多 30 分钟
            time.sleep(2)
            try:
                url = win.evaluate_js("window.location.href") or ""
                if not url:
                    continue
                m = re.search(r"/workspace/(wrk_[A-Za-z0-9]+)/", url)
                if not m:
                    continue
                ws = m.group(1)
                auth = ""
                try:
                    cookies = win.get_cookies() or []
                except Exception:
                    cookies = []
                for c in cookies:
                    if getattr(c, "name", "") == "auth":
                        v = getattr(c, "value", "") or ""
                        if v:
                            auth = v
                            break
                if auth:
                    cfg = load_config()
                    srv = cfg.get("server") or {}
                    srv["auth_cookie"] = auth
                    srv["workspace_id"] = ws
                    cfg["server"] = srv
                    save_config(cfg)
                    trigger_sync()
                    try:
                        win.evaluate_js("document.title = '登录成功 - 已保存并同步，可关闭此窗口'")
                    except Exception:
                        pass
                    return
            except Exception:
                pass
        try:
            win.evaluate_js("document.title = '未检测到登录 - 请登录后保持在此页面'")
        except Exception:
            pass

    threading.Thread(target=watcher, daemon=True).start()
    webview.start()


if __name__ == "__main__":
    main()
