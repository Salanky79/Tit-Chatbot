"""Check the HTTP wiring after moving inline UI assets into static files."""

import re
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app import app


class FrontendAssetTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def test_chat_page_loads_its_styles_and_script(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn('/static/css/chat.css', response.text)
        self.assertIn('/static/js/chat.js', response.text)
        # Keep classic-script initialization between the main UI and modal markup.
        self.assertLess(response.text.index('</main>'), response.text.index('/static/js/chat.js'))
        self.assertLess(response.text.index('/static/js/chat.js'), response.text.index('id="authModal"'))

    def test_assets_referenced_by_both_templates_are_served_with_correct_types(self):
        for template in ("index", "admin"):
            html = Path(f"templates/{template}.html").read_text(encoding="utf-8")
            paths = re.findall(r'(?:href|src)="(/static/[^"]+)"', html)
            self.assertEqual(len(paths), 2, template)
            for path in paths:
                with self.subTest(path=path):
                    response = self.client.get(path)
                    self.assertEqual(response.status_code, 200)
                    mime = response.headers["content-type"]
                    self.assertIn("javascript" if path.endswith(".js") else "text/css", mime)
                    self.assertGreater(len(response.content), 100)

    def test_admin_page_still_requires_sign_in(self):
        response = self.client.get("/admin", follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/")


if __name__ == "__main__":
    unittest.main()
