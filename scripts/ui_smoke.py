"""Isolated browser regression checks. Never connects to production APIs or stores.

Run: python scripts/ui_smoke.py
Requires Jinja2 and Playwright (Chrome installed). Test-only dependencies may live
in .cache/ui-tools. Screenshots and results are written to .cache/ui-qa.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".cache/ui-tools"))
from jinja2 import Environment, FileSystemLoader, select_autoescape
from playwright.sync_api import sync_playwright, expect  # type: ignore[import-not-found]

OUTPUT = ROOT / ".cache/ui-qa"
OUTPUT.mkdir(parents=True, exist_ok=True)
ENV = Environment(loader=FileSystemLoader(ROOT / "templates"), autoescape=select_autoescape())


class Preview(BaseHTTPRequestHandler):
    def do_GET(self):
        name = {"/": "index.html", "/admin": "admin.html"}.get(urlparse(self.path).path)
        if not name:
            self.send_error(404)
            return
        content = ENV.get_template(name).render().encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, *_):
        pass


def check_scripts():
    for name in ("index", "admin"):
        html = ENV.get_template(f"{name}.html").render()
        for i, script in enumerate(re.findall(r"<script(?:\s[^>]*)?>(.*?)</script>", html, re.S)):
            if not script.strip():
                continue
            path = OUTPUT / f"{name}-{i}.js"
            path.write_text(script, encoding="utf-8")
            subprocess.run(["node", "--check", str(path)], check=True)


USER = {"id": "ui-test-user", "username": "minh", "full_name": "Minh Nguyễn", "email": "minh@example.test", "role": "admin", "is_active": True}
PROJECT = {"_id": "ui-test-project", "name": "Nghiên cứu sản phẩm", "description": "", "instructions": "", "memory_mode": "project_only", "membership": {"role": "owner"}}
CONVERSATION = {"thread_id": "ui-test-chat", "title": "Ý tưởng cho tuần mới", "is_pinned": False, "updated_at": "2026-11-26T08:00:00Z", "created_at": "2026-11-26T08:00:00Z", "user_id": USER["id"]}


def install_fixtures(page, *, signed_in=False):
    requests = []

    def route_api(route):
        request = route.request
        path = urlparse(request.url).path
        requests.append((request.method, path, request.post_data))
        payload = {"success": True}
        status = 200
        if path == "/api/auth/me":
            payload = {"user": USER} if signed_in else {"user": None}
        elif path == "/api/auth/challenge":
            payload = {"success": True, "challenge": None}
        elif path == "/api/auth/signin":
            payload = {"error": "Thông tin đăng nhập chưa đúng."}
            status = 401
        elif path == "/api/conversations":
            payload = {"conversations": [CONVERSATION] if signed_in else []}
        elif path == "/api/conversations/search":
            payload = {"success": True, "results": [CONVERSATION]}
        elif path.startswith("/api/history/"):
            payload = {"messages": []}
        elif path == "/api/projects":
            payload = {"projects": [PROJECT], "project": PROJECT}
        elif path == "/api/projects/invitations":
            payload = {"invitations": []}
        elif path == "/api/projects/ui-test-project":
            payload = {"project": PROJECT, "membership": {"role": "owner"}}
        elif path.endswith("/members"):
            payload = {"success": True, "members": [{"user_id": "ui-member", "full_name": "An", "role": "chat_only", "status": "accepted"}]}
        elif path.endswith("/conversations") and not path.startswith("/api/admin"):
            payload = {"conversations": []}
        elif path.endswith("/files"):
            payload = {"files": []}
        elif path == "/api/personalization":
            payload = {"success": True, "personalization": {"base_style": "default", "custom_instructions": ""}, "memories": []}
        elif path == "/api/personalization/memory-summary":
            payload = {"success": True, "summary": "Chưa có bộ nhớ.", "memories": []}
        elif path == "/api/upload":
            payload = {"success": True, "message": "Đã nhận tài liệu kiểm thử."}
        elif path.endswith("/messages") and path.startswith("/api/admin"):
            payload = {"conversation": CONVERSATION, "messages": [{"role": "assistant", "content": "Nội dung kiểm thử", "created_at": CONVERSATION["created_at"]}], "page": 1, "page_size": 50, "total": 1}
        elif path.endswith("/conversations") and path.startswith("/api/admin"):
            payload = {"items": [CONVERSATION], "page": 1, "page_size": 20, "total": 1}
        elif path == "/api/admin/users":
            payload = {"items": [USER], "page": 1, "page_size": 25, "total": 1}
        elif path == "/api/admin/documents":
            payload = {"items": [], "page": 1, "page_size": 25, "total": 0}
        elif path == "/api/chat/stream":
            body = '\n\n'.join('data: ' + json.dumps(event) for event in [{"token": "Xin chào! Đây là phản hồi kiểm thử giao diện."}, {"done": True}]) + '\n\n'
            route.fulfill(status=200, content_type="text/event-stream", body=body)
            return
        elif path == "/api/chat/followups":
            payload = {"suggestions": []}
        route.fulfill(status=status, content_type="application/json", body=json.dumps(payload))

    # External assets are deliberately blocked to exercise offline resilience.
    page.route(re.compile(r"^https://"), lambda route: route.abort())
    page.route("**/api/**", route_api)
    return requests


def assert_no_overflow(page):
    size = page.evaluate("({width: innerWidth, scroll: document.documentElement.scrollWidth})")
    assert size["scroll"] <= size["width"], size


def main():
    check_scripts()
    server = ThreadingHTTPServer(("127.0.0.1", 0), Preview)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    checks = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(channel="chrome", headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            errors = []
            console_errors = []
            def collect_console(message):
                if message.type == "error" and not message.text.startswith("Failed to load resource"):
                    console_errors.append(message.text)
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("console", collect_console)
            requests = install_fixtures(page)
            page.goto(base)
            expect(page.locator("#welcomeTitle")).to_be_visible()
            page.screenshot(path=str(OUTPUT / "after-chat-desktop.png"), animations="disabled")
            checks.append("Guest welcome initializes even when external CDNs fail")
            page.locator(".card").first.click()
            expect(page.locator("#messageInput")).to_have_value(re.compile("Tìm kiếm"))
            page.locator("#messageInput").fill("Xin chào")
            page.locator("#sendBtn").click()
            expect(page.locator(".message.assistant")).to_contain_text("phản hồi kiểm thử")
            expect(page.locator(".suggestion-heading")).to_have_count(0)
            checks.append("Suggestion, composer, SSE response and welcome dismissal")
            page.locator(".new-chat").click()
            page.locator("#tempChatToggle").click()
            expect(page.locator("#tempChatToggle")).to_have_attribute("aria-pressed", "true")
            checks.append("Temporary chat state")
            page.locator(".topbar-login-btn").click()
            expect(page.locator("#authModal")).to_be_visible()
            page.locator("#signInUsername").fill("test")
            page.locator("#signInPassword").fill("test-only")
            page.locator("#signInForm button[type=submit]").click()
            expect(page.locator("#authErrorBanner")).to_contain_text("chưa đúng")
            for _ in range(12):
                page.keyboard.press("Tab")
                assert page.evaluate("document.querySelector('#authModal').contains(document.activeElement)")
            page.screenshot(path=str(OUTPUT / "after-auth-desktop.png"))
            page.keyboard.press("Escape")
            expect(page.locator("#authModal")).to_be_hidden()
            assert page.evaluate("!document.querySelector('main').inert")
            checks.append("Authentication validation, focus trap and Escape")
            for width in (320, 375, 768, 1024, 1440, 1920):
                page.set_viewport_size({"width": width, "height": 900})
                page.reload()
                expect(page.locator("#welcomeTitle")).to_be_visible()
                assert_no_overflow(page)
                assert page.locator("#sendBtn").bounding_box()["x"] >= 0
                page.screenshot(path=str(OUTPUT / f"after-chat-{width}.png"), animations="disabled")
                if width <= 768:
                    page.locator("#sidebarToggle").click()
                    expect(page.locator(".sidebar")).to_have_class(re.compile("open"))
                    assert page.evaluate("document.querySelector('main').inert")
                    page.keyboard.press("Escape")
                    expect(page.locator(".sidebar")).not_to_have_class(re.compile("open"))
                    assert page.evaluate("document.activeElement.id === 'sidebarToggle'")
            checks.append("Six widths 320–1920px, drawer focus and no document overflow")
            page.emulate_media(reduced_motion="reduce")
            page.reload()
            expect(page.locator("#welcomeTitle")).to_be_visible()
            duration = page.locator(".card").first.evaluate("el => getComputedStyle(el).animationDuration")
            assert float(duration.removesuffix("s")) < .01
            checks.append("Reduced motion")

            signed = browser.new_page(viewport={"width": 1440, "height": 1000})
            signed.on("pageerror", lambda error: errors.append(str(error)))
            signed.on("console", collect_console)
            signed_requests = install_fixtures(signed, signed_in=True)
            signed.goto(base)
            expect(signed.locator(".history-open-btn")).to_be_visible()
            signed.locator(".history-open-btn").focus()
            signed.locator(".rename-btn").click()
            expect(signed.locator("dialog[open]")).to_be_visible()
            signed.locator('dialog input').fill("Tên mới")
            signed.get_by_role("button", name="Tiếp tục", exact=True).click()
            signed.wait_for_timeout(100)
            assert any(path.endswith("/rename") or (method in ("PUT", "PATCH") and "conversations" in path) for method, path, _ in signed_requests), signed_requests
            signed.locator(".project-list-item").click()
            signed.get_by_role("button", name="Instructions", exact=True).click()
            expect(signed.locator("dialog[open]")).to_contain_text("Cài đặt dự án")
            signed.screenshot(path=str(OUTPUT / "after-project-settings.png"), animations="disabled")
            signed.locator('[name="instructions"]').fill("Trả lời rõ ràng")
            signed.get_by_role("button", name="Lưu thay đổi", exact=True).click()
            signed.wait_for_timeout(100)
            patches = [json.loads(body) for method, path, body in signed_requests if method == "PATCH" and path == "/api/projects/ui-test-project"]
            assert patches and set(patches[-1]) == {"name", "description", "instructions", "memory_mode"}
            signed.get_by_role("button", name="Share", exact=True).click()
            expect(signed.locator("dialog[open]")).to_contain_text("Thành viên dự án")
            signed.keyboard.press("Escape")
            signed.evaluate("openPersonalizationModal()")
            expect(signed.locator("#personalizationModal")).to_be_visible()
            signed.screenshot(path=str(OUTPUT / "after-personalization.png"), animations="disabled")
            signed.keyboard.press("Escape")
            checks.append("Signed-in history rename, project settings payload, members and personalization")
            signed.keyboard.press("Control+k")
            expect(signed.locator("#searchChatsModal")).to_be_visible()
            signed.locator("#searchChatsInput").fill("Ý tưởng")
            expect(signed.locator("#searchChatsResults")).to_contain_text("Ý tưởng cho tuần mới")
            signed.keyboard.press("Escape")
            for width in (320, 768):
                signed.set_viewport_size({"width": width, "height": 800})
                for modal, opener in [("profileModal", "openProfileModal()"), ("personalizationModal", "openPersonalizationModal()")]:
                    signed.evaluate(opener)
                    expect(signed.locator(f"#{modal}")).to_be_visible()
                    assert_no_overflow(signed)
                    box = signed.locator(f"#{modal} [role=dialog]")
                    dimensions = box.evaluate("el => ({width:el.clientWidth, scroll:el.scrollWidth})")
                    assert dimensions["scroll"] <= dimensions["width"], (modal, width, dimensions)
                    signed.screenshot(path=str(OUTPUT / f"after-{modal}-{width}.png"), animations="disabled")
                    signed.keyboard.press("Escape")
                signed.evaluate("void editProjectSettings()")
                expect(signed.locator("dialog[open]")).to_be_visible()
                dimensions = signed.locator("dialog[open]").evaluate("el => ({width:el.clientWidth, scroll:el.scrollWidth})")
                assert dimensions["scroll"] <= dimensions["width"], dimensions
                signed.keyboard.press("Escape")
            signed.set_viewport_size({"width": 1440, "height": 1000})
            signed.evaluate("exitProject()")
            signed.locator("#fileInput").set_input_files({"name": "ui-check.txt", "mimeType": "text/plain", "buffer": b"UI test only"})
            expect(signed.locator("#attachmentRow")).to_be_visible()
            signed.locator("#messageInput").fill("Tóm tắt tài liệu")
            signed.locator("#sendBtn").click()
            expect(signed.locator("#attachmentRow")).to_be_hidden()
            expect(signed.locator(".message.assistant").last).to_contain_text("phản hồi kiểm thử")
            checks.append("Search, profile/personalization/project dialogs at 320 and 768px, attachment upload flow")

            admin = browser.new_page(viewport={"width": 1440, "height": 1000})
            admin.on("pageerror", lambda error: errors.append(str(error)))
            admin.on("console", collect_console)
            admin_requests = install_fixtures(admin, signed_in=True)
            admin.goto(base + "/admin")
            expect(admin.locator("#usersBody")).to_contain_text("Minh")
            admin.screenshot(path=str(OUTPUT / "after-admin-desktop.png"), full_page=True, animations="disabled")
            admin.locator('[data-action="view-user-conversations"]').click()
            expect(admin.locator("#conversationsList")).to_contain_text(CONVERSATION["title"])
            admin.locator('[data-action="view-conversation"]').click()
            expect(admin.locator("#historyMessages")).to_contain_text("Nội dung kiểm thử")
            admin.locator('[data-action="close-user-history"]').click()
            admin.locator('[data-action="delete-user"]').click()
            expect(admin.locator("#dialog")).to_be_visible()
            admin.keyboard.press("Escape")
            expect(admin.locator("#dialog")).to_be_hidden()
            assert not any(method == "DELETE" for method, _, _ in admin_requests)
            admin.set_viewport_size({"width": 320, "height": 900})
            assert_no_overflow(admin)
            admin.screenshot(path=str(OUTPUT / "after-admin-mobile.png"), full_page=True, animations="disabled")
            checks.append("Admin tables, responsive overflow boundary, destructive action cancellation")
            assert not errors, errors
            assert not console_errors, console_errors
            checks.append("No uncaught browser runtime errors")
            browser.close()
    finally:
        server.shutdown()
    report = {"checks": checks, "runtime_errors": errors, "console_errors": console_errors, "scope": "Isolated fixtures; external CDNs blocked; no live API or production data. Expected blocked-resource and sign-in 401 console messages excluded."}
    (OUTPUT / "results.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
