from __future__ import annotations

import json
import unittest
import xml.etree.ElementTree as ET
from unittest.mock import patch

from smapi_browser import (
    Account,
    DESKTOP_USER_AGENT,
    DesktopBrowseSession,
    LocalSoapFault,
    SOAP_ENV,
    SMAPI_NS,
    Service,
    SmapiClient,
    SmapiFault,
    account_content_device_id,
    content_auth_header,
    content_browse,
    content_browse_headers,
    content_page_items,
    content_page_sections,
    desktop_content_object_id,
    descendants,
    element_value,
    host_device_id,
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


class ContentTransportTests(unittest.TestCase):
    def test_content_device_identity_uses_account_uid(self) -> None:
        self.assertEqual(
            account_content_device_id("Sonos_household", account()),
            "Sonos_household_00000009",
        )

    def test_desktop_content_id_uses_observed_apple_wrapper(self) -> None:
        apple = Service(204, "Apple Music", "https://example.invalid", "AppLink", 0, {})
        self.assertEqual(
            desktop_content_object_id(apple, "recommendation:AbC/1"),
            "00081024recommendation%3aAbC%2f1",
        )

    def test_content_views_are_flattened_with_section_and_transport(self) -> None:
        rows = content_page_items(
            {
                "views": [
                    {
                        "content": {"container": {"name": "Library"}},
                        "items": [
                            {
                                "id": {"objectId": "libraryfolder:f.2"},
                                "content": {"container": {"name": "Albums", "type": "container"}},
                            }
                        ],
                    }
                ]
            }
        )
        self.assertEqual(rows[0]["section"], "Library")
        self.assertEqual(rows[0]["id"], "libraryfolder:f.2")
        self.assertEqual(rows[0]["source_transport"], "content")

    def test_content_root_exposes_sections_before_embedded_items(self) -> None:
        sections = content_page_sections(
            {
                "views": [
                    {
                        "id": {"objectId": "view:library"},
                        "total": 1,
                        "content": {"container": {"name": "Library"}},
                        "items": [
                            {
                                "id": {"objectId": "libraryfolder:f.2"},
                                "content": {
                                    "container": {
                                        "name": "Albums",
                                        "type": "container",
                                        "imageUrl": "https://example.invalid/art.png",
                                    }
                                },
                            }
                        ],
                    }
                ]
            }
        )
        self.assertEqual(len(sections), 1)
        self.assertEqual(sections[0]["title"], "Library")
        self.assertEqual(sections[0]["source_transport"], "content-section")
        self.assertEqual(sections[0]["_embedded_items"][0]["title"], "Albums")

    def test_content_track_is_not_presented_as_drillable_collection(self) -> None:
        rows = content_page_items(
            {
                "views": [
                    {
                        "content": {"container": {"name": "Episodes"}},
                        "items": [
                            {
                                "id": {"objectId": "episode:1"},
                                "content": {"track": {"name": "Episode One", "type": "track"}},
                            }
                        ],
                    }
                ]
            }
        )
        self.assertEqual(rows[0]["kind"], "mediaMetadata")

    def test_host_identity_prefers_configured_machine_identifier(self) -> None:
        with patch.dict("os.environ", {"SONOS_HOST_DEVICE_ID": "machine-id"}):
            self.assertEqual(host_device_id("192.0.2.1"), "machine-id")

    def test_smapi_auth_header_keeps_account_identity_distinct(self) -> None:
        value = json.loads(content_auth_header(service("AppLink", 8), account()))
        self.assertEqual(value["accounts"][0]["type"], "TOKEN")
        self.assertEqual(value["accounts"][0]["serviceId"], "42")
        self.assertEqual(value["accounts"][0]["accountId"], account().udn)
        self.assertEqual(value["accounts"][0]["value"], "token")

    def test_provider_browse_uses_bearer_not_aggregate_auth(self) -> None:
        headers = content_browse_headers(
            service("AppLink", (1 << 16) | (1 << 21)),
            account(),
            "device-id",
            controller_id="controller-id",
            correlation_id="correlation-id",
            time_zone="America/Toronto",
            explicit_content=True,
        )
        self.assertEqual(headers["Authorization"], "Bearer token")
        self.assertNotIn("X-Sonos-SMAPI-Auth", headers)
        self.assertEqual(headers["X-Sonos-Device-Id"], "device-id")
        self.assertEqual(headers["X-Sonos-Controller-ID"], "controller-id")
        self.assertEqual(headers["X-Sonos-Corr-Id"], "correlation-id")
        self.assertEqual(headers["User-Agent"], DESKTOP_USER_AGENT)
        self.assertEqual(headers["X-Sonos-Context-TimeZone"], "America/Toronto")
        self.assertEqual(headers["X-Sonos-Context-ContentFiltering"], "explicit")

    def test_provider_browse_refreshes_on_401_and_retries(self) -> None:
        target = service("AppLink")
        refresh = client("AppLink")
        refreshed = account(token="new-token", key="new-key")

        class Response:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return None

            def read(self) -> bytes:
                return b'{}'

        failure = __import__("urllib.error").error.HTTPError(
            "https://example.invalid/browse", 401, "Unauthorized", {}, None
        )
        with patch("smapi_browser.service_content_endpoint", return_value="https://example.invalid/browse"), patch(
            "smapi_browser.urllib.request.urlopen", side_effect=[failure, Response()]
        ) as opened, patch.object(refresh, "refresh_auth_token", return_value=refreshed) as refresh_token:
            self.assertEqual(
                content_browse(target, account(), "device-id", refresh_client=refresh),
                {},
            )
        refresh_token.assert_called_once_with()
        self.assertEqual(opened.call_count, 2)
        second_request = opened.call_args_list[1].args[0]
        self.assertEqual(second_request.get_header("Authorization"), "Bearer new-token")

    def test_provider_browse_rejects_unproven_child_routing(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "child-page routing is not established"):
            content_browse(service("AppLink"), account(), "device-id", "album:123")


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
        instance = client("AppLink", 8, host_device_id="host-device-id")

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
        self.assertIsNone(request.get_header("X-sonos-device-id"))
        self.assertEqual(request.get_header("User-agent"), DESKTOP_USER_AGENT)
        envelope = ET.fromstring(request.data)
        self.assertEqual(descendants(envelope, "deviceId")[0].text, "device-id")

    def test_explicit_refresh_omits_http_bearer_like_desktop(self) -> None:
        instance = client("AppLink", 8)

        class Response:
            status = 200
            headers = {}

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return None

            def read(self) -> bytes:
                return (
                    f'<refreshAuthTokenResponse xmlns="{SMAPI_NS}"><refreshAuthTokenResult>'
                    "<authToken>new-token</authToken><privateKey>new-key</privateKey>"
                    "</refreshAuthTokenResult></refreshAuthTokenResponse>"
                ).encode()

        with patch("smapi_browser.urllib.request.urlopen", return_value=Response()) as opened:
            instance.refresh_auth_token()
        request = opened.call_args.args[0]
        self.assertIsNone(request.get_header("Authorization"))
        envelope = ET.fromstring(request.data)
        self.assertEqual(descendants(envelope, "token")[0].text, "token")
        self.assertEqual(descendants(envelope, "key")[0].text, "key")

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
