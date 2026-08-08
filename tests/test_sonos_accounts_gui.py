from __future__ import annotations

import unittest
from unittest.mock import Mock

from smapi_browser import Service
from sonos_accounts_gui import SonosExplorerApp


class AccountGuiTests(unittest.TestCase):
    def test_switching_services_clears_service_specific_values(self) -> None:
        class Value:
            def __init__(self, value: str = "") -> None:
                self.value = value

            def get(self) -> str:
                return self.value

            def set(self, value: str) -> None:
                self.value = value

        app = object.__new__(SonosExplorerApp)
        legacy = Service(9, "Legacy", "https://example.invalid", "UserIdPassword", 0, {})
        linked = Service(37, "Linked", "https://example.invalid", "AppLink", 0, {})
        app.onboarding_services = {"Legacy — 9": legacy, "Linked — 37": linked}
        app.onboarding_service_var = Value("Legacy — 9")
        app.onboarding_session = None
        app.onboarding_url_var = Value()
        app.onboarding_username_var = Value()
        app.onboarding_password_var = Value()
        app.onboarding_nickname_var = Value()
        app.onboarding_auth_var = Value()
        app.onboarding_username_entry = Mock()
        app.onboarding_password_entry = Mock()

        app._onboarding_service_selected()
        app.onboarding_session = object()
        app.onboarding_url_var.set("https://login.example")
        app.onboarding_username_var.set("first-user")
        app.onboarding_password_var.set("first-password")
        app.onboarding_nickname_var.set("first-account")
        app.onboarding_service_var.set("Linked — 37")
        app._onboarding_service_selected()

        self.assertIsNone(app.onboarding_session)
        self.assertEqual(app.onboarding_url_var.get(), "")
        self.assertEqual(app.onboarding_username_var.get(), "")
        self.assertEqual(app.onboarding_password_var.get(), "")
        self.assertEqual(app.onboarding_nickname_var.get(), "")


if __name__ == "__main__":
    unittest.main()
