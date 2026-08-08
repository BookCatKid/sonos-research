from __future__ import annotations

import unittest
from unittest.mock import patch

from smapi_browser import Service
from sonos_account_onboarding import (
    LinkSession,
    account_type,
    add_credentials,
    begin_link,
    commit_link,
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


if __name__ == "__main__":
    unittest.main()
