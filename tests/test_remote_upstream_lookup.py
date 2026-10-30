#!/usr/bin/env python3
"""Deterministic coverage for the retained upstream-only version lookup.

The notification and remote-sentinel infrastructure was removed, but the
upstream lookup itself survives in ``check_toolchain``.  These tests keep that
capability pinned independently of any alert, scheduler, or snapshot layer.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from check_toolchain import (
    REMOTE_RESOLVER_HANDLERS,
    REMOTE_TOOL_PROVIDER_REGISTRY,
    REMOTE_VERSION_SOURCES,
    NetworkFetcher,
    RemoteToolProvider,
    resolve_remote_upstream_versions,
    validate_remote_provider_registry,
)


def mock_payload() -> dict:
    return {
        REMOTE_VERSION_SOURCES["Codex CLI"]: {"version": "1.0.0"},
        REMOTE_VERSION_SOURCES["OpenCodex"]: {"version": "2.0.0"},
        REMOTE_VERSION_SOURCES["npm"]: {"version": "3.0.0"},
        REMOTE_VERSION_SOURCES["Wrangler"]: {"version": "4.0.0"},
        REMOTE_VERSION_SOURCES["Node.js"]: [{"version": "v24.20.0"}],
        REMOTE_VERSION_SOURCES["Git"]: {
            "tag_name": "v2.52.0.windows.1",
            "html_url": "https://github.com/git-for-windows/git/releases/tag/v2.52.0.windows.1",
        },
        REMOTE_VERSION_SOURCES["Bun"]: {
            "tag_name": "bun-v1.2.0",
            "html_url": "https://github.com/oven-sh/bun/releases/tag/bun-v1.2.0",
        },
    }


class RemoteUpstreamLookupTests(unittest.TestCase):
    def test_supported_allowlist_reuses_public_sources(self) -> None:
        self.assertEqual(
            set(REMOTE_VERSION_SOURCES),
            {"Codex CLI", "OpenCodex", "Node.js", "npm", "Git", "Wrangler", "Bun"},
        )

    def test_static_provider_registry_preserves_existing_sources_and_resolvers(self) -> None:
        self.assertEqual(
            [(provider.name, provider.resolver) for provider in REMOTE_TOOL_PROVIDER_REGISTRY],
            [
                ("Codex CLI", "npm_latest"),
                ("OpenCodex", "npm_latest"),
                ("Node.js", "node_stable"),
                ("npm", "npm_latest"),
                ("Git", "github_release"),
                ("Wrangler", "npm_latest"),
                ("Bun", "github_release"),
            ],
        )
        self.assertEqual(
            {provider.name: provider.source for provider in REMOTE_TOOL_PROVIDER_REGISTRY},
            REMOTE_VERSION_SOURCES,
        )
        self.assertEqual(
            [provider.name for provider in sorted(REMOTE_TOOL_PROVIDER_REGISTRY, key=lambda provider: provider.fetch_order)],
            ["Codex CLI", "OpenCodex", "npm", "Wrangler", "Node.js", "Git", "Bun"],
        )

    def test_static_resolver_handlers_cover_all_registry_resolvers(self) -> None:
        self.assertEqual(
            set(REMOTE_RESOLVER_HANDLERS),
            {provider.resolver for provider in REMOTE_TOOL_PROVIDER_REGISTRY},
        )

    def test_static_provider_registry_passes_integrity_validation(self) -> None:
        validate_remote_provider_registry(REMOTE_TOOL_PROVIDER_REGISTRY, REMOTE_RESOLVER_HANDLERS)

    def test_resolvers_use_deterministic_mocked_public_sources(self) -> None:
        versions, failures = resolve_remote_upstream_versions(NetworkFetcher(mock_data=mock_payload()))
        self.assertEqual(failures, {})
        self.assertEqual(set(versions), set(REMOTE_VERSION_SOURCES))
        self.assertEqual(
            versions["Codex CLI"],
            {"latest_version": "1.0.0", "release_url": "https://www.npmjs.com/package/@openai/codex/v/1.0.0"},
        )
        self.assertEqual(versions["Node.js"]["latest_version"], "24.20.0")
        self.assertEqual(versions["Node.js"]["release_url"], "https://nodejs.org/en/blog/release/v24.20.0")
        self.assertEqual(
            versions["Git"],
            {
                "latest_version": "2.52.0",
                "release_url": "https://github.com/git-for-windows/git/releases/tag/v2.52.0.windows.1",
            },
        )

    def test_single_unreachable_source_fails_only_that_provider(self) -> None:
        payload = mock_payload()
        payload[REMOTE_VERSION_SOURCES["Bun"]] = {"tag_name": "not-a-version"}
        versions, failures = resolve_remote_upstream_versions(NetworkFetcher(mock_data=payload))
        self.assertEqual(set(failures), {"Bun"})
        self.assertNotIn("Bun", versions)
        self.assertEqual(set(versions), set(REMOTE_VERSION_SOURCES) - {"Bun"})

    def test_lookup_never_inspects_a_local_executable(self) -> None:
        fetcher = Mock()
        fetcher.fetch_json.side_effect = lambda source: mock_payload()[source]
        versions, failures = resolve_remote_upstream_versions(fetcher)
        self.assertEqual(failures, {})
        self.assertEqual(len(versions), len(REMOTE_VERSION_SOURCES))
        for call in fetcher.fetch_json.call_args_list:
            self.assertTrue(call.args[0].startswith("https://"))

    def test_duplicate_provider_name_fails_validation_before_fetch(self) -> None:
        registry = (
            RemoteToolProvider("Duplicate", "https://example.test/one", "npm_latest", "https://example.test/releases/{version}", 0),
            RemoteToolProvider("Duplicate", "https://example.test/two", "npm_latest", "https://example.test/releases/{version}", 1),
        )
        fetcher = Mock()
        with patch("check_toolchain.REMOTE_TOOL_PROVIDER_REGISTRY", registry):
            with self.assertRaisesRegex(ValueError, "duplicate provider name: Duplicate"):
                resolve_remote_upstream_versions(fetcher)
        fetcher.fetch_json.assert_not_called()

    def test_duplicate_fetch_order_fails_validation_before_fetch(self) -> None:
        registry = (
            RemoteToolProvider("One", "https://example.test/one", "npm_latest", "https://example.test/releases/{version}", 0),
            RemoteToolProvider("Two", "https://example.test/two", "npm_latest", "https://example.test/releases/{version}", 0),
        )
        fetcher = Mock()
        with patch("check_toolchain.REMOTE_TOOL_PROVIDER_REGISTRY", registry):
            with self.assertRaisesRegex(ValueError, "duplicate fetch order: 0"):
                resolve_remote_upstream_versions(fetcher)
        fetcher.fetch_json.assert_not_called()

    def test_unknown_resolver_fails_validation_before_fetch(self) -> None:
        provider = RemoteToolProvider("Unknown", "https://example.test/unknown", "unknown", "https://example.test/releases", 0)
        fetcher = Mock()
        with patch("check_toolchain.REMOTE_TOOL_PROVIDER_REGISTRY", (provider,)):
            with self.assertRaisesRegex(ValueError, "unsupported resolver: unknown"):
                resolve_remote_upstream_versions(fetcher)
        fetcher.fetch_json.assert_not_called()

    def test_empty_required_provider_metadata_fails_validation_before_fetch(self) -> None:
        cases = {
            "name": (RemoteToolProvider("", "https://example.test/source", "npm_latest", "https://example.test/releases/{version}", 0), "empty provider name"),
            "source": (RemoteToolProvider("Empty source", "", "npm_latest", "https://example.test/releases/{version}", 0), "empty source"),
            "release URL template": (RemoteToolProvider("Empty release URL", "https://example.test/source", "npm_latest", "", 0), "empty release URL template"),
        }

        for field, (provider, message) in cases.items():
            with self.subTest(field=field):
                fetcher = Mock()
                with patch("check_toolchain.REMOTE_TOOL_PROVIDER_REGISTRY", (provider,)):
                    with self.assertRaisesRegex(ValueError, message):
                        resolve_remote_upstream_versions(fetcher)
                fetcher.fetch_json.assert_not_called()


if __name__ == "__main__":
    unittest.main()
