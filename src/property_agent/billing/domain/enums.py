from enum import StrEnum


class FeeType(StrEnum):
    PROPERTY = "PROPERTY"
    WATER = "WATER"
    ELECTRICITY = "ELECTRICITY"
    PARKING = "PARKING"


class PaymentStatus(StrEnum):
    UNPAID = "UNPAID"
    PAID = "PAID"
    OVERDUE = "OVERDUE"
