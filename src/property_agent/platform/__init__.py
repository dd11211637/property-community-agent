"""Shared platform contracts used by business modules."""

from property_agent.platform.context import RequestContext
from property_agent.platform.errors import BusinessError
from property_agent.platform.roles import Role

__all__ = ["BusinessError", "RequestContext", "Role"]
