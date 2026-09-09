"""CarbonX API bridge — BYOK, provider-abstraction HTTP API.

See README.md for architecture, security model, and the Lumen integration.
"""

__version__ = "1.3.0"

from .server import App, create_server  # noqa: F401
from .errors import ApiError  # noqa: F401