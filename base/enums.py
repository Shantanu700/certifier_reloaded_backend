from enum import IntEnum, StrEnum

# Write your enums here


class Status(IntEnum):
    CREATED = 0
    UPDATED = 1
    DELETED = 2

class RoleEnum(StrEnum):
    EMPLOYEE = "employee"
    HR = "hr"
