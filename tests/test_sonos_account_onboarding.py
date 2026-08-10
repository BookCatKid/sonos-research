from __future__ import annotations

import unittest
from unittest.mock import patch

from smapi_browser import LocalSoapFault, Service
from sonos_account_onboarding import (
    LinkSession,
    account_type,
    add_credentials,
    begin_link,
    commit_link,
    edit_account_md,
    edit_account_password,
    get_web_code,
    refresh_account_credentials,
    remove_account,
    set_nickname,
)

SERVICE = Service(204, "Apple Music", "https://example.invalid", "AppLink", 0, {})
SUCCESS = b'''<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">
<s:Body><u:AddOAuthAccountXResponse xmlns:u="urn:schemas-upnp-org:service:SystemProperties:1">
<AccountUDN>SA_RINCON52231_X_#Svc52231-1-Token</AccountUDN><AccountNickname>Person</AccountNickname>
</u:AddOAuthAccountXResponse></s:Body></s:Envelope>'''
HOUSEHOLD = b'''<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">
<s:Body><u:GetHouseholdIDResponse xmlns:u="urn:schemas-upnp-org:service:DeviceProperties:1">
<CurrentHouseholdID>Sonos_hh</CurrentHouseholdID></u:GetHouseholdIDResponse></s:Body></s:Envelope>'''


class AccountOnboardingTests(unittest.TestCase):
    def test_account_type_encodes_service_and_schema(self) -> None:
        self.assertEqual(account_type(204), 52231)

    @patch("sonos_account_onboarding.local_soap", side_effect=[HOUSEHOLD, SUCCESS])
    def test_commit_link_uses_native_add_oauth_contract(self, soap) -> None:
        session = LinkSession(204, "Apple Music", "AppLink", "Sonos_hh", 52231,
                              "https://login", "code", "device", "callback")
        added = commit_link("192.0.2.1", SERVICE, session)
        self.assertEqual(added.account_udn, "SA_RINCON52231_X_#Svc52231-1-Token")
        fields = soap.call_args_list[1].args[4]
        self.assertEqual(soap.call_args_list[1].args[3], "AddOAuthAccountX")
        self.assertEqual(fields["AccountType"], "52231")
        self.assertEqual(fields["AuthorizationCode"], "code")
        self.assertEqual(fields["OAuthDeviceID"], "device")
        self.assertEqual(fields["RedirectURI"], "callback")

    def test_link_for_wrong_service_is_rejected_before_network(self) -> None:
        session = LinkSession(37, "SiriusXM", "AppLink", "Sonos_hh", account_type(37),
                              "https://login", "code")
        with self.assertRaisesRegex(Exception, "different service"):
            commit_link("192.0.2.1", SERVICE, session)

    def test_link_with_wrong_account_type_is_rejected_before_network(self) -> None:
        session = LinkSession(204, "Apple Music", "AppLink", "Sonos_hh", 52232,
                              "https://login", "code")
        with self.assertRaisesRegex(Exception, "account type does not match"):
            commit_link("192.0.2.1", SERVICE, session)

    @patch("sonos_account_onboarding.local_soap", return_value=HOUSEHOLD.replace(b"Sonos_hh", b"Sonos_other"))
    def test_link_cannot_be_committed_to_another_household(self, _soap) -> None:
        session = LinkSession(204, "Apple Music", "AppLink", "Sonos_hh", 52231,
                              "https://login", "code", "device", "callback")
        with self.assertRaisesRegex(Exception, "player .* belongs to Sonos_other"):
            commit_link("192.0.2.1", SERVICE, session)

    def test_non_web_registration_uri_is_not_openable(self) -> None:
        session = LinkSession(204, "Apple Music", "AppLink", "Sonos_hh", 52231,
                              "dangerous://login", "code")
        self.assertFalse(session.standalone_supported)

    @patch("sonos_account_onboarding._client")
    def test_devicelink_falls_back_to_legacy_link_code(self, make_client) -> None:
        import xml.etree.ElementTree as ET

        legacy = Service(201, "Amazon Music", "https://example.invalid", "DeviceLink", 0, {})
        make_client.return_value._request.side_effect = [
            RuntimeError("getAppLink unsupported"),
            ET.fromstring(
                "<Envelope><getDeviceLinkCodeResult><regUrl>https://login.example/</regUrl>"
                "<linkCode>short-code</linkCode><linkDeviceId>hidden-device</linkDeviceId>"
                "</getDeviceLinkCodeResult></Envelope>"
            ),
        ]
        session = begin_link("192.0.2.1", "Sonos_hh", legacy)
        self.assertEqual(session.source_action, "getDeviceLinkCode")
        self.assertEqual(session.registration_url, "https://login.example/")
        self.assertEqual(session.link_code, "short-code")

    @patch("sonos_account_onboarding._client")
    def test_applink_without_browser_path_is_reported_not_invented(self, make_client) -> None:
        import xml.etree.ElementTree as ET

        make_client.return_value._request.return_value = ET.fromstring(
            "<Envelope><getAppLinkResult><appUrlEncrypt>true</appUrlEncrypt>"
            "</getAppLinkResult></Envelope>"
        )
        session = begin_link("192.0.2.1", "Sonos_hh", SERVICE)
        self.assertFalse(session.standalone_supported)
        self.assertEqual(session.registration_url, "")

    @patch("sonos_account_onboarding.local_soap", side_effect=[HOUSEHOLD, SUCCESS])
    def test_anonymous_service_commits_with_empty_key(self, soap) -> None:
        anonymous = Service(511, "90s90s Radio", "https://example.invalid", "Anonymous", 0, {})
        add_credentials("192.0.2.1", anonymous, "", "", household_id="Sonos_hh")
        self.assertEqual(soap.call_args_list[1].args[3], "AddAccountX")
        fields = soap.call_args_list[1].args[4]
        self.assertEqual(fields["AccountType"], str(account_type(511)))
        self.assertEqual(fields["AccountID"], "")

    @patch("sonos_account_onboarding.local_soap", side_effect=[HOUSEHOLD, SUCCESS])
    def test_legacy_credentials_use_add_account(self, soap) -> None:
        legacy = Service(9, "Legacy", "https://example.invalid", "UserIdPassword", 0, {})
        add_credentials("192.0.2.1", legacy, "user", "pass", household_id="Sonos_hh")
        self.assertEqual(soap.call_args_list[1].args[3], "AddAccountX")
        self.assertEqual(soap.call_args_list[1].args[4]["AccountID"], "user")

    @patch("sonos_account_onboarding.local_soap", return_value=HOUSEHOLD.replace(b"Sonos_hh", b"Sonos_other"))
    def test_legacy_account_rejects_stale_household_before_mutation(self, soap) -> None:
        legacy = Service(9, "Legacy", "https://example.invalid", "UserIdPassword", 0, {})
        with self.assertRaisesRegex(Exception, "player .* belongs to Sonos_other"):
            add_credentials("192.0.2.1", legacy, "user", "pass", household_id="Sonos_hh")
        self.assertEqual(soap.call_count, 1)
        self.assertEqual(soap.call_args.args[3], "GetHouseholdID")

    @patch("sonos_account_onboarding.local_soap")
    def test_missing_legacy_credentials_are_rejected_before_network(self, soap) -> None:
        user_id = Service(8, "User service", "https://example.invalid", "UserId", 0, {})
        password = Service(9, "Password service", "https://example.invalid", "UserIdPassword", 0, {})
        with self.assertRaisesRegex(Exception, "requires a username"):
            add_credentials("192.0.2.1", user_id, "", "", household_id="Sonos_hh")
        with self.assertRaisesRegex(Exception, "requires a password"):
            add_credentials("192.0.2.1", password, "user", "", household_id="Sonos_hh")
        soap.assert_not_called()

    @patch("sonos_account_onboarding.local_soap", side_effect=[HOUSEHOLD, SUCCESS])
    def test_remove_account_uses_native_remove_contract(self, soap) -> None:
        legacy = Service(9, "Legacy", "https://example.invalid", "UserIdPassword", 0, {})
        remove_account("192.0.2.1", legacy, "SA_RINCON2311_X_#Svc2311-1-Token", household_id="Sonos_hh")
        self.assertEqual(soap.call_args_list[1].args[3], "RemoveAccount")
        fields = soap.call_args_list[1].args[4]
        self.assertEqual(fields["AccountType"], str(account_type(9)))
        self.assertEqual(fields["AccountID"], "SA_RINCON2311_X_#Svc2311-1-Token")

    @patch("sonos_account_onboarding.local_soap", side_effect=[HOUSEHOLD, SUCCESS])
    def test_remove_keyless_account_uses_empty_key_contract(self, soap) -> None:
        anonymous = Service(511, "90s90s Radio", "https://example.invalid", "Anonymous", 0, {})
        remove_account("192.0.2.1", anonymous, "SA_RINCON130823_", household_id="Sonos_hh")
        self.assertEqual(soap.call_args_list[1].args[3], "RemoveAccount")
        fields = soap.call_args_list[1].args[4]
        self.assertEqual(fields["AccountType"], str(account_type(511)))
        self.assertEqual(fields["AccountID"], "")

    @patch("sonos_account_onboarding.local_soap", return_value=HOUSEHOLD)
    def test_remove_account_rejects_missing_udn_before_network(self, soap) -> None:
        legacy = Service(9, "Legacy", "https://example.invalid", "UserIdPassword", 0, {})
        with self.assertRaisesRegex(Exception, "account UDN is required"):
            remove_account("192.0.2.1", legacy, "", household_id="Sonos_hh")
        soap.assert_not_called()

    @patch("sonos_account_onboarding.local_soap", side_effect=[HOUSEHOLD, SUCCESS])
    def test_edit_password_uses_native_contract(self, soap) -> None:
        legacy = Service(9, "Legacy", "https://example.invalid", "UserIdPassword", 0, {})
        edit_account_password(
            "192.0.2.1",
            legacy,
            "SA_RINCON2311_X_#Svc2311-1-Token",
            "new-pass",
            household_id="Sonos_hh",
        )
        self.assertEqual(soap.call_args_list[1].args[3], "EditAccountPasswordX")
        fields = soap.call_args_list[1].args[4]
        # AccountID is the account key (Username0), not the full UDN: the player
        # rejects the full UDN for edits (UPnP 806, verified live).
        self.assertEqual(fields["AccountID"], "X_#Svc2311-1-Token")
        self.assertEqual(fields["NewAccountPassword"], "new-pass")

    def test_edit_password_rejects_oauth_service_before_network(self) -> None:
        linked = Service(37, "Linked", "https://example.invalid", "AppLink", 0, {})
        with self.assertRaisesRegex(Exception, "applies to UserIdPassword"):
            edit_account_password(
                "192.0.2.1",
                linked,
                "SA_RINCON9479_X_#Svc9479-1-Token",
                "new-pass",
                household_id="Sonos_hh",
            )
        user_id = Service(8, "User service", "https://example.invalid", "UserId", 0, {})
        with self.assertRaisesRegex(Exception, "applies to UserIdPassword"):
            edit_account_password(
                "192.0.2.1",
                user_id,
                "SA_RINCON2055_X_#Svc2055-1-Token",
                "new-pass",
                household_id="Sonos_hh",
            )

    @patch("sonos_account_onboarding.local_soap", side_effect=[HOUSEHOLD, SUCCESS])
    def test_edit_md_uses_native_contract(self, soap) -> None:
        service = Service(37, "Linked", "https://example.invalid", "AppLink", 0, {})
        edit_account_md(
            "192.0.2.1",
            service,
            "SA_RINCON9479_X_#Svc9479-1-Token",
            "provider-md",
            household_id="Sonos_hh",
        )
        self.assertEqual(soap.call_args_list[1].args[3], "EditAccountMd")
        fields = soap.call_args_list[1].args[4]
        self.assertEqual(fields["AccountID"], "X_#Svc9479-1-Token")
        self.assertEqual(fields["NewAccountMd"], "provider-md")

    @patch("sonos_account_onboarding.local_soap", side_effect=[HOUSEHOLD, SUCCESS])
    def test_set_nickname_encodes_values_in_household_envelope(self, soap) -> None:
        from decode_third_party_media_servers import decrypt_blob

        set_nickname("192.0.2.1", "SA_RINCON9479_X_#Svc9479-1-Token", "New name", household_id="Sonos_hh")
        self.assertEqual(soap.call_args_list[1].args[3], "SetAccountNicknameX")
        fields = soap.call_args_list[1].args[4]
        # Plaintext values are rejected (UPnP 402); the player wants both the
        # UDN and the nickname wrapped in the household 2: envelope.
        self.assertTrue(fields["AccountUDN"].startswith("2:"))
        self.assertTrue(fields["AccountNickname"].startswith("2:"))
        self.assertEqual(decrypt_blob(fields["AccountUDN"], "Sonos_hh"), b"SA_RINCON9479_X_#Svc9479-1-Token")
        self.assertEqual(decrypt_blob(fields["AccountNickname"], "Sonos_hh"), b"New name")

    @patch("sonos_account_onboarding.local_soap", side_effect=[HOUSEHOLD, SUCCESS])
    def test_set_nickname_normalizes_blob_udn_before_encoding(self, soap) -> None:
        from decode_third_party_media_servers import decrypt_blob, encode_blob

        blob = encode_blob(b"SA_RINCON9479_X_#Svc9479-1-Token", "Sonos_hh")
        set_nickname("192.0.2.1", blob, "New name", household_id="Sonos_hh")
        fields = soap.call_args_list[1].args[4]
        # The 2: blob from AddAccountX must be decoded first so it is not
        # double-encoded; the sent AccountUDN decrypts to the plaintext UDN.
        self.assertEqual(decrypt_blob(fields["AccountUDN"], "Sonos_hh"), b"SA_RINCON9479_X_#Svc9479-1-Token")

    def test_set_nickname_translates_player_rejection(self) -> None:
        fault = LocalSoapFault("SetAccountNicknameX", 500, "s:Client", "UPnPError", upnp_code=402)
        with patch("sonos_account_onboarding.local_soap", side_effect=[HOUSEHOLD, fault]):
            with self.assertRaisesRegex(Exception, "UPnP error 402.*No account state was changed"):
                set_nickname("192.0.2.1", "SA_RINCON9479_X_#Svc9479-1-Token", "New name", household_id="Sonos_hh")

    @patch("sonos_account_onboarding.local_soap", side_effect=[HOUSEHOLD, SUCCESS])
    def test_refresh_credentials_uses_native_contract(self, soap) -> None:
        service = Service(37, "Linked", "https://example.invalid", "AppLink", 0, {})
        refresh_account_credentials(
            "192.0.2.1",
            service,
            7,
            "fresh-token",
            "fresh-key",
            household_id="Sonos_hh",
        )
        self.assertEqual(soap.call_args_list[1].args[3], "RefreshAccountCredentialsX")
        fields = soap.call_args_list[1].args[4]
        self.assertEqual(fields["AccountType"], str(account_type(37)))
        self.assertEqual(fields["AccountUID"], "7")
        self.assertEqual(fields["AccountToken"], "fresh-token")
        self.assertEqual(fields["AccountKey"], "fresh-key")

    @patch("sonos_account_onboarding.local_soap", return_value=HOUSEHOLD)
    def test_refresh_credentials_rejects_incomplete_pair_before_network(self, soap) -> None:
        service = Service(37, "Linked", "https://example.invalid", "AppLink", 0, {})
        with self.assertRaisesRegex(Exception, "Both a token and a key"):
            refresh_account_credentials("192.0.2.1", service, 7, "token", "", household_id="Sonos_hh")
        with self.assertRaisesRegex(Exception, "positive numeric AccountUID"):
            refresh_account_credentials("192.0.2.1", service, 0, "token", "key", household_id="Sonos_hh")
        soap.assert_not_called()

    @patch(
        "sonos_account_onboarding.local_soap",
        return_value=b'''<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">
<s:Body><u:GetWebCodeResponse xmlns:u="urn:schemas-upnp-org:service:SystemProperties:1">
<WebCode>1234-5678</WebCode></u:GetWebCodeResponse></s:Body></s:Envelope>''',
    )
    def test_get_web_code_parses_native_result(self, soap) -> None:
        service = Service(37, "Linked", "https://example.invalid", "AppLink", 0, {})
        code = get_web_code("192.0.2.1", service)
        self.assertEqual(code, "1234-5678")
        self.assertEqual(soap.call_args.args[3], "GetWebCode")
        self.assertEqual(soap.call_args.args[4], {"AccountType": str(account_type(37))})

    @patch(
        "sonos_account_onboarding.local_soap",
        return_value=b'''<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">
<s:Body><u:GetWebCodeResponse xmlns:u="urn:schemas-upnp-org:service:SystemProperties:1">
</u:GetWebCodeResponse></s:Body></s:Envelope>''',
    )
    def test_get_web_code_rejects_empty_player_result(self, soap) -> None:
        service = Service(37, "Linked", "https://example.invalid", "AppLink", 0, {})
        with self.assertRaisesRegex(Exception, "returned no web code"):
            get_web_code("192.0.2.1", service)

    @patch(
        "sonos_account_onboarding.local_soap",
        side_effect=LocalSoapFault("GetWebCode", 500, "s:Client", "UPnPError", upnp_code=800),
    )
    def test_get_web_code_translates_player_rejection(self, soap) -> None:
        service = Service(37, "Linked", "https://example.invalid", "AppLink", 0, {})
        with self.assertRaisesRegex(Exception, "UPnP error 800.*no account state was changed"):
            get_web_code("192.0.2.1", service)


if __name__ == "__main__":
    unittest.main()
