from property_agent.platform.errors import BusinessError


def forbidden() -> BusinessError:
    return BusinessError("FORBIDDEN", "You are not allowed to access these bills.", 403)


def not_found() -> BusinessError:
    return BusinessError("RESOURCE_NOT_FOUND", "Bill was not found.", 404)


def validation_error(message: str) -> BusinessError:
    return BusinessError("VALIDATION_ERROR", message, 422)
