"""
Shared request context — single definition for the whole modular monolith.

PRD 5.2 (PF-03 RBAC & data isolation): every business module receives identity
and tenancy through the *same* ``RequestContext`` object produced by the
platform authentication layer. This module is the stable import path
(``property_agent.platform.context``) used by business modules; the concrete
implementation lives with the auth dependencies so that it stays next to the
JWT decoding that populates it.

Do NOT define a second RequestContext anywhere — a divergent copy silently
breaks role checks and community isolation across modules.
"""
from __future__ import annotations

from property_agent.platform.adapters.api.dependencies import RequestContext

__all__ = ["RequestContext"]
