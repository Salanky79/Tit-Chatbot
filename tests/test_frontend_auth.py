import unittest
import os
import re

class TestFrontendAuthUI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        template_path = os.path.join(os.path.dirname(__file__), "..", "templates", "index.html")
        with open(template_path, "r", encoding="utf-8") as f:
            cls.html_content = f.read()

    def test_sidebar_user_section_exists(self):
        self.assertIn('id="sidebarUserSection"', self.html_content)
        self.assertIn('class="sidebar-user-section"', self.html_content)

    def test_topbar_user_area_exists(self):
        self.assertIn('id="topbarUserArea"', self.html_content)
        self.assertIn('class="topbar-right"', self.html_content)

    def test_auth_modal_structure(self):
        self.assertIn('id="authModal"', self.html_content)
        self.assertIn('id="signInForm"', self.html_content)
        self.assertIn('id="signUpForm"', self.html_content)
        self.assertIn('id="tabSignInBtn"', self.html_content)
        self.assertIn('id="tabSignUpBtn"', self.html_content)
        self.assertIn('id="authErrorBanner"', self.html_content)
        self.assertIn('id="signInUsername"', self.html_content)
        self.assertIn('id="signInPassword"', self.html_content)
        self.assertIn('id="signUpUsername"', self.html_content)
        self.assertIn('id="signUpEmail"', self.html_content)
        self.assertIn('id="signUpPassword"', self.html_content)

    def test_auth_javascript_functions(self):
        self.assertIn('function checkAuthStatus()', self.html_content)
        self.assertIn('function renderUserUI()', self.html_content)
        self.assertIn('function openAuthModal(', self.html_content)
        self.assertIn('function closeAuthModal()', self.html_content)
        self.assertIn('function switchAuthTab(', self.html_content)
        self.assertIn('function handleSignInSubmit(', self.html_content)
        self.assertIn('function handleSignUpSubmit(', self.html_content)
        self.assertIn('function handleSignOut()', self.html_content)

    def test_auth_api_endpoints_called_in_frontend(self):
        self.assertIn('fetch("/auth/me")', self.html_content)
        self.assertIn('fetch("/auth/signin"', self.html_content)
        self.assertIn('fetch("/auth/signup"', self.html_content)
        self.assertIn('fetch("/auth/signout"', self.html_content)

if __name__ == "__main__":
    unittest.main()
