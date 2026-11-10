"""Qualification and launch authority of the upstream lazydocker integration."""

from __future__ import annotations

import re
import unittest
from unittest.mock import patch

from rexdeck import dependencies
from rexdeck.catalogue.containers import INTEGRATION
from rexdeck.integrations import Layout, get


class ContainersTests(unittest.TestCase):
    def test_real_multiline_version(self) -> None:
        output = (
            "Version: 0.25.2\n"
            "Date: 2026-04-19T02:50:06Z\n"
            "BuildSource: Homebrew\n"
            "Commit: \n"
            "OS: darwin\n"
            "Arch: arm64\n"
        )
        for text in (output, output.replace("\n", "\r\n")):
            with self.subTest(text=text):
                match = re.search(INTEGRATION.version_pattern, text, re.MULTILINE)
                self.assertIsNotNone(match)
                assert match is not None
                self.assertEqual(match.group(1), "0.25.2")
                self.assertIn(match.group(1), INTEGRATION.versions)

    def test_version_near_misses(self) -> None:
        for output in (
            "lazydocker 0.25.2",
            "NotVersion: 0.25.2",
            "Version: 0.25.2-rc1",
            "Version: 0.25.20garbage",
            "Version: 0.25",
            "Version: 0.25.2.1",
            "Version:\n0.25.2",
        ):
            with self.subTest(output=output):
                self.assertIsNone(
                    re.search(INTEGRATION.version_pattern, output, re.MULTILINE)
                )
        for version in ("0.25.1", "0.25.3", "0.26.0"):
            match = re.search(
                INTEGRATION.version_pattern, f"Version: {version}\n", re.MULTILINE
            )
            self.assertIsNotNone(match)
            assert match is not None
            self.assertNotIn(match.group(1), INTEGRATION.versions)

    def test_descriptor_preserves_context_and_upstream_authority(self) -> None:
        self.assertIs(get("containers"), INTEGRATION)
        self.assertEqual(INTEGRATION.title, "Containers")
        self.assertEqual(INTEGRATION.tool, "lazydocker")
        self.assertEqual(INTEGRATION.context, "directory")
        self.assertEqual(INTEGRATION.versions, ("0.25.2",))
        self.assertEqual(INTEGRATION.license, "MIT")
        self.assertEqual(INTEGRATION.default_layout, Layout("right", 50))
        self.assertEqual(
            INTEGRATION.prerequisites,
            (("docker", "https://docs.docker.com/get-docker/"),),
        )
        # No endpoint, project, compose-file or read-only fiction is injected.
        self.assertEqual(INTEGRATION.args, ())
        self.assertIsNone(INTEGRATION.owned_module)
        self.assertTrue(INTEGRATION.capabilities)
        self.assertEqual(
            INTEGRATION.describe()["capabilities"], list(INTEGRATION.capabilities)
        )

    def test_missing_docker_refuses_before_lazydocker_probe(self) -> None:
        with (
            patch("rexdeck.dependencies._resolve", return_value=None) as resolve,
            patch("rexdeck.dependencies._probe") as probe,
            self.assertRaises(RuntimeError) as failure,
        ):
            dependencies.ensure("containers")
        resolve.assert_called_once_with("docker")
        probe.assert_not_called()
        self.assertIn("https://docs.docker.com/get-docker/", str(failure.exception))
        self.assertIn("rexdeck enable containers", str(failure.exception))


if __name__ == "__main__":
    unittest.main()
