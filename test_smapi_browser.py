from __future__ import annotations

import unittest
import xml.etree.ElementTree as ET
from unittest.mock import patch

from smapi_browser import (
    Account,
    LocalSoapFault,
    SOAP_ENV,
    SMAPI_NS,
    Service,
    SmapiClient,
    SmapiFault,
    descendants,
    element_value,
    local_name,
    parse_accounts,
    redact_diagnostic,
)


def service(auth: str, capabilities: int = 0) -> Service:
    return Service(42, "Test", "https://example.invalid/smapi", auth, capabilities, {})


def account(**changes: object) -> Account:
    values = {
        "service_id": 42,
        "serial": 9,
        "udn": "SA_RINCON10759_X_#Svc10759-9-Token",
        "username": "user",
        "password": "password",
        "token": "token",
        "key": "key",
    }
    values.update(changes)
    return Account(**values)  # type: ignore[arg-type]


def client(auth: str, capabilities: int = 0, **options: object) -> SmapiClient:
    return SmapiClient(
        service(auth, capabilities),
        account(),
        "Sonos_household",
        "device-id",
        "zone-id",
        "192.0.2.1",
        controller_id="controller-id",
        time_zone="America/Toronto",
        **options,
    )


class EnvelopeTests(unittest.TestCase):
    def credential_children(self, instance: SmapiClient, *, mode: str = "normal") -> list[str]:
        root = ET.fromstring(instance._envelope("getMetadata", {}, credential_mode=mode))
        credentials = descendants(root, "credentials")[0]
        return [local_name(node.tag) for node in credentials]

    def test_anonymous_uses_only_device_credentials(self) -> None:
        self.assertEqual(
            self.credential_children(client("Anonymous")),
            ["deviceId", "deviceProvider"],
        )

    def test_applink_embeds_token_key_and_household(self) -> None:
        instance = client("AppLink")
        root = ET.fromstring(instance._envelope("getMetadata", {}))
        token = descendants(root, "loginToken")[0]
        self.assertEqual(
            {local_name(node.tag): node.text for node in token},
            {"token": "token", "key": "key", "householdId": "Sonos_household"},
        )

    def test_bearer_capability_omits_token_and_key_from_soap(self) -> None:
        instance = client("AppLink", 8)
        root = ET.fromstring(instance._envelope("getMetadata", {}))
        token = descendants(root, "loginToken")[0]
        self.assertEqual([local_name(node.tag) for node in token], ["householdId"])

    def test_bearer_refresh_puts_old_token_and_key_back_in_soap(self) -> None:
        instance = client("AppLink", 8)
        root = ET.fromstring(instance._envelope("refreshAuthToken", {}, credential_mode="refresh"))
        token = descendants(root, "loginToken")[0]
        self.assertEqual(
            {local_name(node.tag): node.text for node in token},
            {"token": "token", "key": "key", "householdId": "Sonos_household"},
        )

    def test_context_is_controlled_by_capability_bit_16(self) -> None:
        without = ET.fromstring(client("Anonymous")._envelope("getMetadata", {}))
        with_context = ET.fromstring(client("Anonymous", 1 << 16)._envelope("getMetadata", {}))
        self.assertFalse(descendants(without, "context"))
        self.assertEqual(descendants(with_context, "timeZone")[0].text, "America/Toronto")

    def test_zone_player_id_is_controlled_by_capability_bit_18(self) -> None:
        without = ET.fromstring(client("AppLink")._envelope("getMetadata", {}))
        with_zone = ET.fromstring(client("AppLink", 1 << 18)._envelope("getMetadata", {}))
        self.assertFalse(descendants(without, "zonePlayerId"))
        self.assertEqual(descendants(with_zone, "zonePlayerId")[0].text, "zone-id")

    def test_devicelink_uses_session_id_for_normal_browse(self) -> None:
        instance = client("DeviceLink", 8)
        instance.account = account(token="", key="")
        instance.session_id = "session"
        root = ET.fromstring(instance._envelope("getMetadata", {}))
        self.assertEqual(descendants(root, "sessionId")[0].text, "session")
        self.assertFalse(descendants(root, "loginToken"))

    def test_linked_devicelink_uses_stored_token_instead_of_session(self) -> None:
        instance = client("DeviceLink")
        instance.session_id = "stale-session"
        root = ET.fromstring(instance._envelope("getMetadata", {}))
        token = descendants(root, "loginToken")[0]
        self.assertEqual(
            {local_name(node.tag): node.text for node in token},
            {"token": "token", "key": "key", "householdId": "Sonos_household"},
        )
        self.assertFalse(descendants(root, "sessionId"))

    def test_devicelink_get_session_uses_base_credentials(self) -> None:
        instance = client("DeviceLink", 8)
        root = ET.fromstring(instance._envelope("getSessionId", {}, credential_mode="base"))
        self.assertEqual(
            [local_name(node.tag) for node in descendants(root, "credentials")[0]],
            ["deviceId", "deviceProvider"],
        )

    def test_user_id_policy_uses_login(self) -> None:
        instance = client("UserId")
        root = ET.fromstring(instance._envelope("getMetadata", {}))
        login = descendants(root, "login")[0]
        self.assertEqual(
            {local_name(node.tag): node.text for node in login},
            {"username": "user", "password": "password"},
        )


class ProtocolFlowTests(unittest.TestCase):
    def test_undeclared_xsi_nil_from_provider_is_tolerated(self) -> None:
        instance = client("Anonymous")

        class Response:
            status = 200
            headers = {}

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return None

            def read(self) -> bytes:
                return (
                    f'<soap:Envelope xmlns:soap="{SOAP_ENV}"><soap:Body>'
                    f'<getMetadataResponse xmlns="{SMAPI_NS}" xsi:nil="true"/>'
                    "</soap:Body></soap:Envelope>"
                ).encode()

        with patch("smapi_browser.urllib.request.urlopen", return_value=Response()):
            root = instance._request("getMetadata", {"id": "root"})
        self.assertTrue(descendants(root, "getMetadataResponse"))

    def test_bearer_header_and_controller_header_are_sent(self) -> None:
        instance = client("AppLink", 8)

        class Response:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return None

            def read(self) -> bytes:
                return f'<getMetadataResponse xmlns="{SMAPI_NS}"/>'.encode()

        with patch("smapi_browser.urllib.request.urlopen", return_value=Response()) as opened:
            instance._request("getMetadata", {"id": "root"})
        request = opened.call_args.args[0]
        self.assertEqual(request.get_header("Authorization"), "Bearer token")
        self.assertEqual(request.get_header("X-sonos-controller-id"), "controller-id")

    def test_get_session_id_is_cached_and_inserted(self) -> None:
        instance = client("DeviceLink", 8)
        instance.account = account(token="", key="")
        calls: list[tuple[str, dict[str, str], str]] = []

        def request(action: str, fields: dict[str, str], account=None, *, credential_mode="normal"):
            calls.append((action, fields, credential_mode))
            return ET.fromstring(
                f'<getSessionIdResponse xmlns="{SMAPI_NS}"><getSessionIdResult>s1</getSessionIdResult>'
                "</getSessionIdResponse>"
            )

        instance._request = request  # type: ignore[method-assign]
        self.assertEqual(instance.get_session_id(), "s1")
        self.assertEqual(
            calls,
            [("getSessionId", {"username": "user", "password": "password"}, "base")],
        )
        root = ET.fromstring(instance._envelope("getMetadata", {}))
        self.assertEqual(descendants(root, "sessionId")[0].text, "s1")

    def test_refresh_accepts_the_replacement_pair_in_memory(self) -> None:
        instance = client("AppLink")
        instance._request = lambda *args, **kwargs: ET.fromstring(  # type: ignore[method-assign]
            f'<refreshAuthTokenResponse xmlns="{SMAPI_NS}"><refreshAuthTokenResult>'
            "<authToken>new-token</authToken><privateKey>new-key</privateKey>"
            "</refreshAuthTokenResult></refreshAuthTokenResponse>"
        )
        with patch("smapi_browser.local_soap") as local_request:
            refreshed = instance.refresh_auth_token()
        self.assertEqual((refreshed.token, refreshed.key), ("new-token", "new-key"))
        local_request.assert_not_called()

    def test_embedded_fault_replacement_is_accepted_without_second_refresh_call(self) -> None:
        instance = client("AppLink", allow_credential_refresh=True)
        fault = SmapiFault(
            "Client.TokenRefreshRequired",
            "refresh",
            500,
            {"refreshAuthTokenResult": {"authToken": "embedded-token", "privateKey": "embedded-key"}},
        )
        success = ET.fromstring(f'<getMetadataResponse xmlns="{SMAPI_NS}"/>')
        with patch.object(instance, "_request", side_effect=[fault, success]), patch.object(
            instance, "refresh_auth_token"
        ) as explicit_refresh, patch("smapi_browser.local_soap") as local_request:
            self.assertIs(instance._request_with_refresh("getMetadata", {}), success)
        explicit_refresh.assert_not_called()
        self.assertEqual((instance.account.token, instance.account.key), ("embedded-token", "embedded-key"))
        local_request.assert_not_called()

    def test_missing_embedded_replacement_does_not_make_an_unsupported_refresh_call(self) -> None:
        instance = client("AppLink", allow_credential_refresh=True)
        fault = SmapiFault("Client.AuthTokenExpired", "expired without replacement", 500)
        with patch.object(instance, "_request", side_effect=fault), patch.object(
            instance, "refresh_auth_token"
        ) as explicit_refresh:
            with self.assertRaises(SmapiFault):
                instance._request_with_refresh("getMetadata", {})
        explicit_refresh.assert_not_called()

    def test_refresh_rejects_an_incomplete_replacement_pair(self) -> None:
        instance = client("AppLink")
        instance._request = lambda *args, **kwargs: ET.fromstring(  # type: ignore[method-assign]
            f'<refreshAuthTokenResponse xmlns="{SMAPI_NS}"><refreshAuthTokenResult>'
            "<authToken>new-token</authToken><privateKey/>"
            "</refreshAuthTokenResult></refreshAuthTokenResponse>"
        )
        with patch("smapi_browser.local_soap") as local_request:
            with self.assertRaisesRegex(RuntimeError, "both authToken and privateKey"):
                instance.refresh_auth_token()
        local_request.assert_not_called()

    def test_needs_reauth_is_not_sent_to_a_provider(self) -> None:
        instance = client("AppLink")
        instance.account = account(token="needs_reauth")
        with self.assertRaisesRegex(SmapiFault, "NeedsReauthorization"):
            instance._request_with_refresh("getMetadata", {})

    def test_expiration_does_not_refresh_in_read_only_mode(self) -> None:
        instance = client("AppLink")
        fault = SmapiFault("Client.AuthTokenExpired", "expired", 500)
        with patch.object(instance, "_request", side_effect=fault), patch.object(
            instance, "refresh_auth_token"
        ) as refresh:
            with self.assertRaises(SmapiFault):
                instance._request_with_refresh("getMetadata", {})
        refresh.assert_not_called()

    def test_expiration_refreshes_and_retries_when_enabled(self) -> None:
        instance = client("AppLink", 8, allow_credential_refresh=True)
        fault = SmapiFault("Client.AuthTokenExpired", "expired", 500)
        success = ET.fromstring(f'<getMetadataResponse xmlns="{SMAPI_NS}"/>')
        with patch.object(instance, "_request", side_effect=[fault, success]) as request, patch.object(
            instance, "refresh_auth_token"
        ) as refresh:
            self.assertIs(instance._request_with_refresh("getMetadata", {}), success)
        refresh.assert_called_once_with()
        self.assertEqual(request.call_count, 2)

    def test_invalid_devicelink_session_is_replaced_once(self) -> None:
        instance = client("DeviceLink")
        instance.account = account(token="", key="")
        instance.session_id = "old-session"
        calls: list[str] = []

        def request(action: str, fields: dict[str, str], account=None, *, credential_mode="normal"):
            calls.append(action)
            if calls == ["getMetadata"]:
                raise SmapiFault("Client.InvalidSessionId", "Invalid session", 500)
            if action == "getSessionId":
                return ET.fromstring(
                    f'<getSessionIdResponse xmlns="{SMAPI_NS}"><getSessionIdResult>new-session'
                    "</getSessionIdResult></getSessionIdResponse>"
                )
            return ET.fromstring(f'<getMetadataResponse xmlns="{SMAPI_NS}"/>')

        instance._request = request  # type: ignore[method-assign]
        instance._request_with_refresh("getMetadata", {})
        self.assertEqual(calls, ["getMetadata", "getSessionId", "getMetadata"])
        self.assertEqual(instance.session_id, "new-session")

    def test_search_count_is_capped_at_one_thousand(self) -> None:
        instance = client("Anonymous")
        seen: dict[str, str] = {}

        def request(action: str, fields: dict[str, str]) -> ET.Element:
            seen.update(fields)
            return ET.fromstring(
                f'<searchResponse xmlns="{SMAPI_NS}"><searchResult><count>0</count><total>0</total>'
                "</searchResult></searchResponse>"
            )

        instance._request_with_refresh = request  # type: ignore[method-assign]
        instance.search("artist", "term", 990, 100)
        self.assertEqual(seen["count"], "10")


class ParserTests(unittest.TestCase):
    def test_fault_diagnostics_redact_embedded_refresh_credentials(self) -> None:
        value = {
            "refreshAuthTokenResult": {"authToken": "secret", "privateKey": "also-secret"},
            "reason": "expired",
        }
        self.assertEqual(
            redact_diagnostic(value),
            {
                "refreshAuthTokenResult": {
                    "authToken": {"redacted": True, "length": 6},
                    "privateKey": {"redacted": True, "length": 11},
                },
                "reason": "expired",
            },
        )

    def test_account_parser_preserves_distinct_auth_fields(self) -> None:
        payload = (
            '<MediaServers><Service UDN="SA_RINCON10759_test" SerialNum0="9" '
            'Username0="u" Password0="p" Token0="t" Key0="k" Nickname0="n" Tier0="2"/>'
            "</MediaServers>"
        ).encode()
        parsed = parse_accounts(payload)[0]
        self.assertEqual(
            (
                parsed.service_id,
                parsed.schema_revision,
                parsed.serial,
                parsed.username,
                parsed.password,
                parsed.token,
                parsed.key,
            ),
            (42, 7, 9, "u", "p", "t", "k"),
        )

    def test_nested_metadata_is_not_discarded(self) -> None:
        root = ET.fromstring("<item><title>T</title><metadata><description>D</description></metadata></item>")
        self.assertEqual(element_value(root), {"title": "T", "metadata": {"description": "D"}})


if __name__ == "__main__":
    unittest.main()
