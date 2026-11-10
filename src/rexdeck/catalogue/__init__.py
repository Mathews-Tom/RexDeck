"""One module per integration, each exporting its descriptor as ``INTEGRATION``.

``rexdeck.integrations`` discovers these modules; importing it first means any
direct import of a catalogue module sees a fully validated catalogue.
"""

from __future__ import annotations

import rexdeck.integrations  # noqa: F401  (discovery owns catalogue import order)
