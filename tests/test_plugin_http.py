"""Slumber version parsing and argument-free directory launch contract."""

from __future__ import annotations

import re
import unittest

from rexdeck.catalogue.http import INTEGRATION
from rexdeck.integrations import INTEGRATIONS, Layout


class HttpPluginTests(unittest.TestCase):
    def test_real_version_output(self) -> None:
        for output in ("slumber 5.4.4\n", "slumber 5.4.4\r\n"):
            with self.subTest(output=output):
                match = re.search(INTEGRATION.version_pattern, output, re.MULTILINE)
                self.assertIsNotNone(match)
                assert match is not None
                self.assertIn(match[1], INTEGRATION.versions)

    def test_near_misses_do_not_qualify(self) -> None:
        for output in (
            "slumber 5.4.3\n",
            "slumber 5.4.5\n",
            "slumber 5.4.4-rc1\n",
            "slumber 5.4.4 extra\n",
            "slumber 5.4.40\n",
            "slumber 5.4\n",
            "5.4.4\n",
            "not-slumber 5.4.4\n",
            "Slumber 5.4.4\n",
        ):
            with self.subTest(output=output):
                match = re.search(INTEGRATION.version_pattern, output, re.MULTILINE)
                self.assertFalse(match and match[1] in INTEGRATION.versions)

    def test_discovered_directory_tui_has_no_arguments(self) -> None:
        self.assertIs(INTEGRATIONS["http"], INTEGRATION)
        self.assertEqual(INTEGRATION.title, "HTTP")
        self.assertEqual(INTEGRATION.tool, "slumber")
        self.assertEqual(INTEGRATION.context, "directory")
        self.assertEqual(INTEGRATION.versions, ("5.4.4",))
        self.assertEqual(INTEGRATION.license, "MIT")
        self.assertEqual(INTEGRATION.default_layout, Layout("right", 50))
        # No request subcommand, file override, secret or remote-control option.
        self.assertEqual(INTEGRATION.args, ())
        self.assertEqual(INTEGRATION.prerequisites, ())
        self.assertIsNone(INTEGRATION.owned_module)
        self.assertTrue(INTEGRATION.capabilities)
