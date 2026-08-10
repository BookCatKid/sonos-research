from __future__ import annotations

import json
import unittest
from unittest.mock import Mock, call, patch

from smapi_browser import Account, Service
import sonos_account_onboarding as onboarding
from sonos_accounts_gui import SonosExplorerApp


def _account(service_id: int = 37, serial: int = 1, **fields: str) -> Account:
    return Account(
        service_id,
        serial,
        f"SA_RINCON{service_id * 256 + 7}_X_#Svc{service_id * 256 + 7}-1-Token",
        **fields,
    )


class AccountGuiTests(unittest.TestCase):
    def test_apple_music_selection_explains_mobile_only_linking(self) -> None:
        class Value:
            def __init__(self, value: str = "") -> None:
                self.value = value

            def get(self) -> str:
                return self.value

            def set(self, value: str) -> None:
                self.value = value

        app = object.__new__(SonosExplorerApp)
        apple = Service(204, "Apple Music", "https://example.invalid", "AppLink", 0, {})
        app.onboarding_services = {"Apple Music — 204": apple}
        app.onboarding_service_var = Value("Apple Music — 204")
        app.onboarding_session = None
        app.onboarding_url_var = Value()
        app.onboarding_username_var = Value()
        app.onboarding_password_var = Value()
        app.onboarding_nickname_var = Value()
        app.onboarding_auth_var = Value()
        app.onboarding_username_entry = Mock()
        app.onboarding_password_entry = Mock()

        app._onboarding_service_selected()
        note = app.onboarding_auth_var.get()
        self.assertIn("app-to-app linking only", note)
        self.assertIn("iOS/Android", note)
        self.assertIn("no browser URL", note)

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

    def _manage_app(self) -> SonosExplorerApp:
        app = object.__new__(SonosExplorerApp)
        legacy = Service(9, "Legacy", "https://example.invalid", "UserIdPassword", 0, {})
        linked = Service(37, "Linked", "https://example.invalid", "AppLink", 0, {})
        app.manage_services = {9: legacy, 37: linked}
        app.manage_accounts = {
            "manage-9-1": (legacy, _account(9, 1)),
            "manage-37-1": (linked, _account(37, 1)),
        }
        app.manage_tree = Mock()
        app.manage_tree.selection.return_value = []
        app.manage_remove_button = Mock()
        app.manage_rename_button = Mock()
        app.manage_password_button = Mock()
        app.manage_reauthorize_button = Mock()
        app.manage_details = Mock()
        app.host_var = Mock()
        app.host_var.get.return_value = "192.0.2.1"
        app.household_var = Mock()
        app.household_var.get.return_value = "Sonos_hh"
        app.timeout_var = Mock()
        app.timeout_var.get.return_value = "3"
        app.wait_var = Mock()
        app.wait_var.get.return_value = "8"
        app.port_var = Mock()
        app.port_var.get.return_value = "3411"
        app.root = Mock()
        app.busy = False
        app.summary_var = Mock()
        app._log = Mock()
        return app

    def test_manage_selection_controls_password_action_by_auth_type(self) -> None:
        app = self._manage_app()
        app.manage_tree.selection.return_value = ["manage-9-1"]
        app._manage_account_selected()
        app.manage_password_button.configure.assert_called_once_with(state="normal")

        app.manage_tree.selection.return_value = ["manage-37-1"]
        app._manage_account_selected()
        app.manage_password_button.configure.assert_called_with(state="disabled")
        self.assertEqual(app.manage_reauthorize_button.configure.call_count, 2)

    def test_user_id_service_without_password_disables_password_edit(self) -> None:
        app = self._manage_app()
        user_id = Service(8, "User service", "https://example.invalid", "UserId", 0, {})
        app.manage_accounts["manage-8-1"] = (user_id, _account(8, 1))
        app.manage_tree.selection.return_value = ["manage-8-1"]
        app._manage_account_selected()
        app.manage_password_button.configure.assert_called_with(state="disabled")

    def test_keyless_account_is_flagged_in_details(self) -> None:
        app = self._manage_app()
        anonymous = Service(511, "90s90s Radio", "https://example.invalid", "Anonymous", 0, {})
        keyless = Account(511, 39, "SA_RINCON130823_")
        app.manage_accounts["manage-511-39"] = (anonymous, keyless)
        app.manage_tree.selection.return_value = ["manage-511-39"]
        app._set_text = Mock()
        app._manage_account_selected()
        payload = json.loads(app._set_text.call_args.args[1])
        self.assertIn("keyless_record", payload)
        self.assertIn("empty-key RemoveAccount contract", payload["keyless_record"])

    def test_keyless_account_enables_remove_and_rename(self) -> None:
        app = self._manage_app()
        anonymous = Service(511, "90s90s Radio", "https://example.invalid", "Anonymous", 0, {})
        keyless = Account(511, 39, "SA_RINCON130823_")
        app.manage_accounts["manage-511-39"] = (anonymous, keyless)
        app.manage_tree.selection.return_value = ["manage-511-39"]
        app._set_text = Mock()
        app._manage_account_selected()
        # Keyless records resolve for removal with the empty-key contract, and
        # rename works through the 2:-encoded envelope.
        app.manage_remove_button.configure.assert_called_with(state="normal")
        app.manage_rename_button.configure.assert_called_with(state="normal")

    def test_manage_accounts_complete_flags_keyless_state_column(self) -> None:
        app = self._manage_app()
        app.notebook = Mock()
        app.manage_tree.get_children.return_value = []
        anonymous = Service(511, "90s90s Radio", "https://example.invalid", "Anonymous", 0, {})
        linked = Service(37, "Linked", "https://example.invalid", "AppLink", 0, {})
        app._manage_accounts_complete(
            (
                "Sonos_hh",
                {511: anonymous, 37: linked},
                [Account(511, 39, "SA_RINCON130823_"), _account(37, 1, token="tok", key="key")],
            )
        )
        states = [call.kwargs["values"][-1] for call in app.manage_tree.insert.call_args_list]
        self.assertIn("keyless", states)
        self.assertIn("linked", states)

    def test_manage_no_selection_disables_all_actions(self) -> None:
        app = self._manage_app()
        app._manage_account_selected()
        app.manage_remove_button.configure.assert_called_once_with(state="disabled")
        app.manage_rename_button.configure.assert_called_once_with(state="disabled")
        app.manage_reauthorize_button.configure.assert_called_once_with(state="disabled")

    def test_manage_remove_uses_native_remove_contract(self) -> None:
        app = self._manage_app()
        app.manage_tree.selection.return_value = ["manage-9-1"]
        app._manage_account_selected()
        with patch("sonos_accounts_gui.onboarding.player_household", return_value="Sonos_hh"), patch(
            "sonos_accounts_gui.messagebox.askyesno", return_value=True
        ), patch("sonos_accounts_gui.onboarding.remove_account") as remove, patch.object(
            SonosExplorerApp, "_run_task", side_effect=lambda label, work, success: success(work())
        ), patch("sonos_accounts_gui.messagebox.showinfo"):
            app.manage_remove_account()
        remove.assert_called_once()
        args = remove.call_args.args
        kwargs = remove.call_args.kwargs
        self.assertEqual(args[0], "192.0.2.1")
        self.assertEqual(args[2], _account(9, 1).udn)
        self.assertEqual(kwargs["household_id"], "Sonos_hh")

    def test_manage_remove_keyless_passes_truncated_udn(self) -> None:
        app = self._manage_app()
        anonymous = Service(511, "90s90s Radio", "https://example.invalid", "Anonymous", 0, {})
        keyless = Account(511, 39, "SA_RINCON130823_")
        app.manage_accounts["manage-511-39"] = (anonymous, keyless)
        app.manage_tree.selection.return_value = ["manage-511-39"]
        app._manage_account_selected()
        with patch("sonos_accounts_gui.onboarding.player_household", return_value="Sonos_hh"), patch(
            "sonos_accounts_gui.messagebox.askyesno", return_value=True
        ), patch("sonos_accounts_gui.onboarding.remove_account") as remove, patch.object(
            SonosExplorerApp, "_run_task", side_effect=lambda label, work, success: success(work())
        ), patch("sonos_accounts_gui.messagebox.showinfo"):
            app.manage_remove_account()
        remove.assert_called_once()
        # The keyless record's truncated UDN is passed through; the empty-key
        # contract is applied inside onboarding.remove_account.
        self.assertEqual(remove.call_args.args[2], "SA_RINCON130823_")

    def test_manage_reauthorize_replaces_selected_account_in_place(self) -> None:
        app = self._manage_app()
        app.manage_tree.selection.return_value = ["manage-37-1"]
        app._manage_account_selected()
        session = onboarding.LinkSession(
            37,
            "Linked",
            "AppLink",
            "Sonos_hh",
            onboarding.account_type(37),
            "https://login.example/",
            "code",
        )
        with patch("sonos_accounts_gui.onboarding.begin_link", return_value=session), patch(
            "sonos_accounts_gui.messagebox.askyesno", return_value=True
        ), patch.object(
            SonosExplorerApp, "_run_task", side_effect=lambda label, work, success: success(work())
        ), patch("sonos_accounts_gui.webbrowser.open"), patch(
            "sonos_accounts_gui.onboarding.commit_link"
        ) as commit, patch("sonos_accounts_gui.messagebox.showinfo"), patch(
            "sonos_accounts_gui.secrets.token_urlsafe", return_value="state"
        ):
            app.manage_reauthorize()
        # Reauthorizing an existing account mirrors the official controller's
        # per-account replace action: fresh credentials are committed through
        # ReplaceAccountX against the selected record's UDN instead of adding a
        # duplicate AddOAuthAccountX account.
        commit.assert_called_once()
        self.assertEqual(commit.call_args.args[0], "192.0.2.1")
        self.assertEqual(commit.call_args.args[2], session)
        self.assertEqual(commit.call_args.kwargs["replace_account_udn"], _account(37, 1).udn)

    def test_manage_rename_enabled_for_keyed_account(self) -> None:
        app = self._manage_app()
        app.manage_accounts["manage-37-1"] = (app.manage_services[37], _account(37, 1, token="tok", key="key"))
        app.manage_tree.selection.return_value = ["manage-37-1"]
        app._manage_account_selected()
        self.assertEqual(app.manage_rename_button.configure.call_args_list[-1], call(state="normal"))
        with patch("sonos_accounts_gui.simpledialog.askstring", return_value="New Name"), patch(
            "sonos_accounts_gui.onboarding.player_household", return_value="Sonos_hh"
        ), patch("sonos_accounts_gui.messagebox.askyesno", return_value=True), patch(
            "sonos_accounts_gui.onboarding.set_nickname"
        ) as rename, patch.object(
            SonosExplorerApp, "_run_task", side_effect=lambda label, work, success: success(work())
        ), patch("sonos_accounts_gui.messagebox.showinfo"):
            app.manage_set_nickname()
        rename.assert_called_once()
        self.assertEqual(rename.call_args.args[0], "192.0.2.1")
        self.assertEqual(rename.call_args.args[2], "New Name")
        self.assertEqual(rename.call_args.kwargs["household_id"], "Sonos_hh")

    @staticmethod
    def _commit_app() -> "SonosExplorerApp":
        class Value:
            def __init__(self) -> None:
                self.value = ""

            def get(self) -> str:
                return self.value

            def set(self, value: str) -> None:
                self.value = value

        app = object.__new__(SonosExplorerApp)
        app.onboarding_password_var = Value()
        app.onboarding_session = None
        app.onboarding_auth_var = Value()
        app._onboarding_commit_target = ("192.0.2.1", "Sonos_hh")
        app.root = Mock()
        app.root.after.side_effect = lambda _delay, fn, *args: fn()
        return app

    def test_commit_prompts_with_provider_nickname_prefilled(self) -> None:
        app = self._commit_app()
        added = onboarding.AddedAccount(
            37, "SiriusXM", "SA_RINCON9479_X_#Svc9479-1-Token",
            nickname="", provider_nickname="BookCatKid",
        )
        with patch("sonos_accounts_gui.simpledialog.askstring", return_value="BookCatKid") as ask, patch(
            "sonos_accounts_gui.onboarding.set_nickname"
        ) as rename, patch.object(
            SonosExplorerApp, "_run_task", side_effect=lambda label, work, success: success(work())
        ), patch("sonos_accounts_gui.messagebox.showinfo"):
            app._onboarding_commit_complete(added)
        # The official app flow: the provider's account name (Spotify's
        # "BookCatKid") pre-fills the nickname prompt, and the choice is applied
        # with SetAccountNicknameX right after the account commit.
        ask.assert_called_once()
        self.assertEqual(ask.call_args.kwargs["initialvalue"], "BookCatKid")
        rename.assert_called_once_with(
            "192.0.2.1", added.account_udn, "BookCatKid", household_id="Sonos_hh"
        )

    def test_commit_skips_prompt_when_nickname_was_chosen_in_advance(self) -> None:
        app = self._commit_app()
        added = onboarding.AddedAccount(
            37, "SiriusXM", "SA_RINCON9479_X_#Svc9479-1-Token",
            nickname="My Music", provider_nickname="BookCatKid",
        )
        with patch("sonos_accounts_gui.simpledialog.askstring") as ask, patch(
            "sonos_accounts_gui.onboarding.set_nickname"
        ) as rename, patch("sonos_accounts_gui.messagebox.showinfo"):
            app._onboarding_commit_complete(added)
        ask.assert_not_called()
        rename.assert_not_called()

    def test_commit_skips_prompt_when_provider_has_no_account_name(self) -> None:
        app = self._commit_app()
        added = onboarding.AddedAccount(37, "SiriusXM", "SA_RINCON9479_X_#Svc9479-1-Token")
        with patch("sonos_accounts_gui.simpledialog.askstring") as ask, patch(
            "sonos_accounts_gui.onboarding.set_nickname"
        ) as rename, patch("sonos_accounts_gui.messagebox.showinfo"):
            app._onboarding_commit_complete(added)
        ask.assert_not_called()
        rename.assert_not_called()


if __name__ == "__main__":
    unittest.main()
