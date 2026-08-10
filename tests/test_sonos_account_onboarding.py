from __future__ import annotations

import unittest
from unittest.mock import patch

from smapi_browser import Account, LocalSoapFault, Service
from sonos_account_onboarding import (
    DeviceAuthCredential,
    LinkSession,
    OnboardingError,
    account_type,
    add_credentials,
    begin_link,
    commit_link,
    edit_account_md,
    edit_account_password,
    get_device_auth_token,
    get_web_code,
    refresh_account_credentials,
    remove_account,
    replace_account_credentials,
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

    @patch(
        "sonos_account_onboarding.get_device_auth_token",
        return_value=DeviceAuthCredential(
            auth_token="BQBJ-token",
            private_key="priv-key",
            user_id_hash_code="Fi0Z-hash",
            nickname="BookCatKid",
        ),
    )
    @patch("sonos_account_onboarding.local_soap", side_effect=[HOUSEHOLD, SUCCESS])
    def test_commit_link_uses_captured_add_oauth_contract(self, soap, _gdat) -> None:
        from decode_third_party_media_servers import decrypt_blob

        session = LinkSession(204, "Apple Music", "AppLink", "Sonos_hh", 52231,
                              "https://login", "code", "device", "callback")
        added = commit_link("192.0.2.1", SERVICE, session)
        self.assertEqual(added.account_udn, "SA_RINCON52231_X_#Svc52231-1-Token")
        fields = soap.call_args_list[1].args[4]
        self.assertEqual(soap.call_args_list[1].args[3], "AddOAuthAccountX")
        self.assertEqual(fields["AccountType"], "52231")
        # Every account value is wrapped in the household 2: envelope and the
        # authorization code / redirect URI stay empty -- the provider
        # credential package is what is installed.
        self.assertEqual(fields["AuthorizationCode"], "")
        self.assertEqual(fields["RedirectURI"], "")
        self.assertEqual(fields["AccountTier"], "1")
        self.assertEqual(decrypt_blob(fields["AccountToken"], "Sonos_hh"), b"BQBJ-token")
        # The provider's key already carries its own epoch stamp, so it is
        # enveloped verbatim.
        self.assertEqual(decrypt_blob(fields["AccountKey"], "Sonos_hh"), b"priv-key")
        self.assertEqual(decrypt_blob(fields["OAuthDeviceID"], "Sonos_hh"), b"Sonos_hh")
        self.assertEqual(decrypt_blob(fields["UserIdHashCode"], "Sonos_hh"), b"Fi0Z-hash")
        # The link code itself is exchanged with the provider first, never sent
        # to the player (which rejects it with UPnP 402).
        _gdat.assert_called_once_with("192.0.2.1", "Sonos_hh", SERVICE, "code", "device")

    @patch(
        "sonos_account_onboarding.get_device_auth_token",
        return_value=DeviceAuthCredential(
            auth_token="BQBJ-token", private_key="priv-key", nickname="BookCatKid"
        ),
    )
    @patch("sonos_account_onboarding.local_soap", side_effect=[HOUSEHOLD, SUCCESS])
    def test_commit_link_surfaces_provider_nickname_for_prefill(self, soap, _gdat) -> None:
        session = LinkSession(204, "Apple Music", "AppLink", "Sonos_hh", 52231,
                              "https://login", "code", "device", "callback")
        added = commit_link("192.0.2.1", SERVICE, session)
        # The provider's userInfo.nickname (the account holder's screen name)
        # is surfaced separately so the controller can pre-fill its nickname
        # prompt with it; the player's own stored nickname comes back from
        # AddOAuthAccountX unchanged.
        self.assertEqual(added.provider_nickname, "BookCatKid")
        self.assertEqual(added.nickname, "Person")

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

    @patch(
        "sonos_account_onboarding.get_device_auth_token",
        return_value=DeviceAuthCredential(auth_token="BQBJ-token", private_key="priv-key"),
    )
    @patch("sonos_account_onboarding.local_soap", side_effect=[HOUSEHOLD, SUCCESS])
    def test_commit_link_omits_empty_user_id_hash(self, soap, _gdat) -> None:
        # Providers whose getDeviceAuthToken returns no userIdHashCode must not
        # produce an enveloped empty blob; the field is simply left empty.
        session = LinkSession(204, "Apple Music", "AppLink", "Sonos_hh", 52231,
                              "https://login", "code")
        commit_link("192.0.2.1", SERVICE, session)
        fields = soap.call_args_list[1].args[4]
        self.assertEqual(fields["UserIdHashCode"], "")
        self.assertEqual(fields["AccountTier"], "1")

    @patch(
        "sonos_account_onboarding.get_device_auth_token",
        return_value=DeviceAuthCredential(
            auth_token="BQBJ-token",
            private_key="priv-key/1786401533373",
            user_id_hash_code="1b406fc7825ba31162c8ed926084b4b5",
            nickname="BookCatKid",
        ),
    )
    @patch("sonos_account_onboarding.local_soap", side_effect=[HOUSEHOLD, SUCCESS])
    def test_commit_link_converts_hex_user_id_hash_to_base64(self, soap, _gdat) -> None:
        # Verified live by replaying the captured Spotify commit: the player
        # accepts UserIdHashCode only as base64.  The provider currently
        # returns the hash as hex (32 hex chars); the same bytes committed as
        # base64 return 200 while the raw hex form is rejected with 402.
        from decode_third_party_media_servers import decrypt_blob

        session = LinkSession(204, "Apple Music", "AppLink", "Sonos_hh", 52231,
                              "https://login", "code")
        commit_link("192.0.2.1", SERVICE, session)
        fields = soap.call_args_list[1].args[4]
        stored_hash = decrypt_blob(fields["UserIdHashCode"], "Sonos_hh").decode()
        self.assertEqual(stored_hash, "G0Bvx4JboxFiyO2SYIS0tQ==")

    @patch(
        "sonos_account_onboarding.get_device_auth_token",
        return_value=DeviceAuthCredential(
            auth_token="BQBJ-token",
            private_key="priv-key/1786401533373",
            user_id_hash_code="1b406fc7825ba31162c8ed926084b4b5",
            nickname="BookCatKid",
        ),
    )
    @patch("sonos_account_onboarding.local_soap", side_effect=[HOUSEHOLD, SUCCESS])
    def test_commit_link_always_commits_record_flag_tier(self, soap, _gdat) -> None:
        # The provider's deprecated userInfo.accountTier string (``free``/
        # ``premium``/``trial``) must never reach the player -- sending it raw is
        # rejected with UPnP 402.  The player's AccountTier is a record flag, so
        # the commit always sends ``1``.
        session = LinkSession(204, "Apple Music", "AppLink", "Sonos_hh", 52231,
                              "https://login", "code")
        commit_link("192.0.2.1", SERVICE, session)
        fields = soap.call_args_list[1].args[4]
        self.assertEqual(fields["AccountTier"], "1")
        # The provider key already carries its epoch stamp; it is stored verbatim.
        from decode_third_party_media_servers import decrypt_blob

        self.assertEqual(
            decrypt_blob(fields["AccountKey"], "Sonos_hh"),
            b"priv-key/1786401533373",
        )

    @patch(
        "sonos_account_onboarding.get_device_auth_token",
        side_effect=OnboardingError("the link code may have expired"),
    )
    @patch("sonos_account_onboarding.local_soap", return_value=HOUSEHOLD)
    def test_commit_link_failed_exchange_never_mutates_player(self, soap, _gdat) -> None:
        # If the provider exchange fails, the player must not be touched: only
        # the read-only GetHouseholdID check runs, never AddOAuthAccountX.
        session = LinkSession(204, "Apple Music", "AppLink", "Sonos_hh", 52231,
                              "https://login", "code")
        with self.assertRaisesRegex(Exception, "link code may have expired"):
            commit_link("192.0.2.1", SERVICE, session)
        self.assertEqual(soap.call_count, 1)
        self.assertEqual(soap.call_args.args[3], "GetHouseholdID")

    @patch(
        "sonos_account_onboarding.get_device_auth_token",
        return_value=DeviceAuthCredential(auth_token="BQBJ-token", private_key="priv-key"),
    )
    @patch(
        "sonos_account_onboarding.inventory",
        return_value=(
            {},
            [Account(12, 50, "SA_RINCON3079_X_#Svc3079-0-Token", username="X_#Svc3079-0-Token", nickname="Spotify 50")],
        ),
    )
    @patch(
        "sonos_account_onboarding.local_soap",
        side_effect=[
            HOUSEHOLD,
            LocalSoapFault("AddOAuthAccountX", 500, "s:Client", "UPnPError", upnp_code=402),
        ],
    )
    def test_commit_link_explains_existing_duplicate_account(self, soap, _inv, _gdat) -> None:
        # Verified live: re-committing the same Spotify user while its record is
        # already in the household is rejected with UPnP 402; the player stores
        # the capture's own token as the account.  The fault is translated into
        # an actionable duplicate-account message instead of a bare error.
        session = LinkSession(12, "Spotify", "AppLink", "Sonos_hh", account_type(12),
                              "https://login", "code")
        spotify = Service(12, "Spotify", "https://example.invalid", "AppLink", 0, {})
        with self.assertRaisesRegex(
            Exception, "already linked.*Spotify 50.*Reauthorize the existing account in place"
        ):
            commit_link("192.0.2.1", spotify, session)
        self.assertEqual(soap.call_args_list[1].args[3], "AddOAuthAccountX")

    @patch(
        "sonos_account_onboarding.get_device_auth_token",
        return_value=DeviceAuthCredential(
            auth_token="BQBJ-fresh",
            private_key="priv-key/1786401533373",
            nickname="BookCatKid",
        ),
    )
    @patch(
        "sonos_account_onboarding.local_soap",
        side_effect=[HOUSEHOLD, HOUSEHOLD, SUCCESS],
    )
    def test_commit_link_replace_path_replaces_in_place(self, soap, _gdat) -> None:
        # Re-linking an existing account mirrors the desktop controller's commit
        # dispatcher: the record keeps its UDN and ReplaceAccountX swaps only
        # the credential package, instead of committing a duplicate
        # AddOAuthAccountX record (which the player rejects with 402).
        session = LinkSession(204, "Apple Music", "AppLink", "Sonos_hh", 52231,
                              "https://login", "code", "device", "callback")
        added = commit_link(
            "192.0.2.1",
            SERVICE,
            session,
            replace_account_udn="SA_RINCON52231_X_#Svc52231-1-Token",
        )
        self.assertEqual(soap.call_args_list[2].args[3], "ReplaceAccountX")
        self.assertEqual(added.account_udn, "SA_RINCON52231_X_#Svc52231-1-Token")
        # Like the add path, the provider's screen name is surfaced for the
        # nickname-prefill flow even when the credentials are replaced in place.
        self.assertEqual(added.provider_nickname, "BookCatKid")

    @patch("sonos_account_onboarding.local_soap", side_effect=[HOUSEHOLD, SUCCESS])
    def test_replace_account_credentials_uses_native_replace_contract(self, soap) -> None:
        from decode_third_party_media_servers import decrypt_blob

        # ReplaceAccountX's argument list is verified against both the desktop
        # decomp (FUN_100e61e60 / FUN_1004aced0) and the player's live
        # SystemProperties SCPD: AccountUDN, NewAccountID, NewAccountPassword,
        # AccountToken, AccountKey, OAuthDeviceID, NewAccountUDN.
        credential = DeviceAuthCredential(auth_token="BQBJ-fresh", private_key="priv-key/1786401533373")
        added = replace_account_credentials(
            "192.0.2.1",
            SERVICE,
            "SA_RINCON52231_X_#Svc52231-1-Token",
            credential,
            household_id="Sonos_hh",
        )
        self.assertEqual(soap.call_args_list[1].args[3], "ReplaceAccountX")
        fields = soap.call_args_list[1].args[4]
        self.assertEqual(
            list(fields),
            [
                "AccountUDN",
                "NewAccountID",
                "NewAccountPassword",
                "AccountToken",
                "AccountKey",
                "OAuthDeviceID",
                "NewAccountUDN",
            ],
        )
        # OAuth-style services leave the legacy credential pair and the new UDN
        # empty, exactly like the desktop's own replace commit.
        self.assertEqual(fields["NewAccountID"], "")
        self.assertEqual(fields["NewAccountPassword"], "")
        self.assertEqual(fields["NewAccountUDN"], "")
        # Credential values follow the AddOAuthAccountX envelope contract.
        self.assertEqual(
            decrypt_blob(fields["AccountUDN"], "Sonos_hh"),
            b"SA_RINCON52231_X_#Svc52231-1-Token",
        )
        self.assertEqual(decrypt_blob(fields["AccountToken"], "Sonos_hh"), b"BQBJ-fresh")
        self.assertEqual(decrypt_blob(fields["AccountKey"], "Sonos_hh"), b"priv-key/1786401533373")
        self.assertEqual(decrypt_blob(fields["OAuthDeviceID"], "Sonos_hh"), b"Sonos_hh")
        # ReplaceAccountX has no output arguments (SCPD); the existing UDN is
        # reported unchanged.
        self.assertEqual(added.account_udn, "SA_RINCON52231_X_#Svc52231-1-Token")

    @patch("sonos_account_onboarding.local_soap", side_effect=[HOUSEHOLD, SUCCESS])
    def test_replace_account_credentials_normalizes_blob_udn(self, soap) -> None:
        from decode_third_party_media_servers import decrypt_blob, encode_blob

        blob = encode_blob(b"SA_RINCON52231_X_#Svc52231-1-Token", "Sonos_hh")
        credential = DeviceAuthCredential(auth_token="BQBJ-fresh", private_key="priv-key")
        replace_account_credentials(
            "192.0.2.1", SERVICE, blob, credential, household_id="Sonos_hh"
        )
        fields = soap.call_args_list[1].args[4]
        # The 2: blob from the inventory must be decoded first so the sent
        # AccountUDN decrypts to the plaintext UDN (no double encoding).
        self.assertEqual(
            decrypt_blob(fields["AccountUDN"], "Sonos_hh"),
            b"SA_RINCON52231_X_#Svc52231-1-Token",
        )

    @patch("sonos_account_onboarding.local_soap", side_effect=[HOUSEHOLD, SUCCESS])
    def test_replace_account_credentials_rejects_incomplete_package(self, soap) -> None:
        credential = DeviceAuthCredential(auth_token="", private_key="")
        with self.assertRaisesRegex(Exception, "complete credential package"):
            replace_account_credentials(
                "192.0.2.1", SERVICE, "SA_RINCON52231_X_#Svc52231-1-Token", credential,
                household_id="Sonos_hh",
            )
        self.assertEqual(soap.call_count, 0)

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
    def test_applink_app_only_marker_raises_actionable_error(self, make_client) -> None:
        import xml.etree.ElementTree as ET

        # Apple Music returns exactly this stub for every platform identity: an
        # encrypted app-link marker with no browser/device-link path.
        make_client.return_value._request.return_value = ET.fromstring(
            "<Envelope><getAppLinkResult><callToAction />"
            "<appUrlEncrypt>true</appUrlEncrypt></getAppLinkResult></Envelope>"
        )
        with self.assertRaisesRegex(Exception, "app-to-app linking only.*Sonos mobile app"):
            begin_link("192.0.2.1", "Sonos_hh", SERVICE)

    @patch("sonos_account_onboarding._client")
    def test_applink_with_real_app_url_is_still_usable(self, make_client) -> None:
        import xml.etree.ElementTree as ET

        # A provider that actually returns an appUrl keeps the app-link path;
        # the stub detection must not reject a genuinely returned app URL.
        make_client.return_value._request.return_value = ET.fromstring(
            "<Envelope><getAppLinkResult><appUrl>apple-music://authorize</appUrl>"
            "<appUrlEncrypt>true</appUrlEncrypt></getAppLinkResult></Envelope>"
        )
        session = begin_link("192.0.2.1", "Sonos_hh", SERVICE)
        self.assertEqual(session.app_url, "apple-music://authorize")

    @patch("sonos_account_onboarding._client")
    def test_devicelink_app_only_marker_still_falls_back_to_link_code(self, make_client) -> None:
        import xml.etree.ElementTree as ET

        # A DeviceLink service whose getAppLink returns the app-only marker must
        # not raise: it keeps the legacy getDeviceLinkCode fallback contract.
        legacy = Service(201, "Amazon Music", "https://example.invalid", "DeviceLink", 0, {})
        make_client.return_value._request.side_effect = [
            ET.fromstring(
                "<Envelope><getAppLinkResult><callToAction />"
                "<appUrlEncrypt>true</appUrlEncrypt></getAppLinkResult></Envelope>"
            ),
            ET.fromstring(
                "<Envelope><getDeviceLinkCodeResult><regUrl>https://login.example/</regUrl>"
                "<linkCode>short-code</linkCode></getDeviceLinkCodeResult></Envelope>"
            ),
        ]
        session = begin_link("192.0.2.1", "Sonos_hh", legacy)
        self.assertEqual(session.source_action, "getDeviceLinkCode")
        self.assertEqual(session.registration_url, "https://login.example/")

    @patch("sonos_account_onboarding._client")
    def test_applink_without_appurl_returns_plain_session(self, make_client) -> None:
        import xml.etree.ElementTree as ET

        # No appUrlEncrypt marker at all: fall back to the previous behavior so
        # other providers keep returning their (empty) session for the caller
        # to interpret.
        make_client.return_value._request.return_value = ET.fromstring(
            "<Envelope><getAppLinkResult><callToAction /></getAppLinkResult></Envelope>"
        )
        session = begin_link("192.0.2.1", "Sonos_hh", SERVICE)
        self.assertFalse(session.standalone_supported)
        self.assertEqual(session.registration_url, "")

    @patch("sonos_account_onboarding._client")
    def test_get_device_auth_token_exchanges_link_code_and_parses_user_info(self, make_client) -> None:
        import xml.etree.ElementTree as ET

        make_client.return_value._request.return_value = ET.fromstring(
            "<Envelope><getDeviceAuthTokenResult><authToken>BQBJ-token</authToken>"
            "<privateKey>priv-key</privateKey>"
            "<userInfo><userIdHashCode>Fi0Z-hash</userIdHashCode><accountTier>1</accountTier>"
            "<nickname>BookCatKid</nickname></userInfo></getDeviceAuthTokenResult></Envelope>"
        )
        with patch("sonos_account_onboarding.player_device_id", return_value="R_TrialZPSerialABC"):
            credential = get_device_auth_token("192.0.2.1", "Sonos_hh", SERVICE, "code", "")
        self.assertEqual(credential.auth_token, "BQBJ-token")
        self.assertEqual(credential.private_key, "priv-key")
        self.assertEqual(credential.user_id_hash_code, "Fi0Z-hash")
        # The provider's deprecated accountTier string is deliberately NOT
        # carried (it must never reach AddOAuthAccountX); nickname is kept.
        self.assertFalse(hasattr(credential, "account_tier"))
        self.assertEqual(credential.nickname, "BookCatKid")
        action, fields = make_client.return_value._request.call_args.args
        self.assertEqual(action, "getDeviceAuthToken")
        self.assertEqual(fields["householdId"], "Sonos_hh")
        self.assertEqual(fields["linkCode"], "code")
        # Providers that omit linkDeviceId fall back to the controller's own
        # R_TrialZPSerial (python-soco's exact fallback).
        self.assertEqual(fields["linkDeviceId"], "R_TrialZPSerialABC")

    @patch("sonos_account_onboarding._client")
    def test_get_device_auth_token_rejects_incomplete_credential_pair(self, make_client) -> None:
        import xml.etree.ElementTree as ET

        make_client.return_value._request.return_value = ET.fromstring(
            "<Envelope><getDeviceAuthTokenResult><authToken>only-token</authToken>"
            "</getDeviceAuthTokenResult></Envelope>"
        )
        with self.assertRaisesRegex(Exception, "no authToken/privateKey pair"):
            get_device_auth_token("192.0.2.1", "Sonos_hh", SERVICE, "code", "device")

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
        # rejects the full UDN for edits (UPnP 806).
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
