from enum import StrEnum

from property_agent.platform.roles import Role


class AnnouncementCategory(StrEnum):
    GENERAL = "GENERAL"
    MAINTENANCE = "MAINTENANCE"
    SAFETY = "SAFETY"
    EMERGENCY = "EMERGENCY"


class AnnouncementStatus(StrEnum):
    DRAFT = "DRAFT"
    PENDING_REVIEW = "PENDING_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    PUBLISHED = "PUBLISHED"
    ARCHIVED = "ARCHIVED"
    WITHDRAWN = "WITHDRAWN"


class AnnouncementAction(StrEnum):
    CREATE = "CREATE"
    EDIT = "EDIT"
    SUBMIT_REVIEW = "SUBMIT_REVIEW"
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    PUBLISH = "PUBLISH"
    SCHEDULE = "SCHEDULE"
    WITHDRAW = "WITHDRAW"
    ARCHIVE = "ARCHIVE"


class VersionSource(StrEnum):
    MANUAL = "MANUAL"
    AI_SUGGESTION_ADOPTED = "AI_SUGGESTION_ADOPTED"


class DeliveryStatus(StrEnum):
    PENDING = "PENDING"
    DELIVERED = "DELIVERED"
    FAILED = "FAILED"


CREATE_ROLES = (Role.CUSTOMER_SERVICE, Role.MANAGER)
READ_ROLES = (Role.RESIDENT, Role.CUSTOMER_SERVICE, Role.MANAGER)
REVIEW_ROLES = (Role.MANAGER,)
