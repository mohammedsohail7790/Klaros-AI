from enum import StrEnum


class Role(StrEnum):
    OWNER = "OWNER"
    ADMIN = "ADMIN"
    MANAGER = "MANAGER"
    STAFF = "STAFF"
    TECHNICIAN = "TECHNICIAN"
    ACCOUNTANT = "ACCOUNTANT"
    READ_ONLY = "READ_ONLY"


class Permission(StrEnum):
    READ_CUSTOMERS = "READ_CUSTOMERS"
    CREATE_CUSTOMER = "CREATE_CUSTOMER"
    UPDATE_CUSTOMER = "UPDATE_CUSTOMER"
    READ_JOBS = "READ_JOBS"
    CREATE_JOB = "CREATE_JOB"
    SCHEDULE_JOB = "SCHEDULE_JOB"
    APPROVE_SPEND = "APPROVE_SPEND"
    CREATE_INVOICE = "CREATE_INVOICE"
    SEND_INVOICE = "SEND_INVOICE"
    COLLECT_PAYMENT = "COLLECT_PAYMENT"
    VIEW_FINANCIALS = "VIEW_FINANCIALS"
    RUN_MARKETING = "RUN_MARKETING"
    SEND_CUSTOMER_MESSAGE = "SEND_CUSTOMER_MESSAGE"
    MANAGE_INTEGRATIONS = "MANAGE_INTEGRATIONS"
    MANAGE_USERS = "MANAGE_USERS"
    EXECUTE_AI_ACTION = "EXECUTE_AI_ACTION"
    DELETE_CUSTOMER = "DELETE_CUSTOMER"
    READ_LEADS = "READ_LEADS"
    CREATE_LEAD = "CREATE_LEAD"
    UPDATE_LEAD = "UPDATE_LEAD"
    QUALIFY_LEAD = "QUALIFY_LEAD"
    READ_APPOINTMENTS = "READ_APPOINTMENTS"
    CREATE_APPOINTMENT = "CREATE_APPOINTMENT"
    CANCEL_APPOINTMENT = "CANCEL_APPOINTMENT"


# Permission matrix: role -> allowed permissions.
# OWNER/ADMIN get everything; DELETE_CUSTOMER is OWNER-only (section 4/10: destructive ops are
# BLOCKED or OWNER ONLY).
_ALL_PERMISSIONS = set(Permission)

ROLE_PERMISSIONS: dict[Role, set[Permission]] = {
    Role.OWNER: _ALL_PERMISSIONS,
    Role.ADMIN: _ALL_PERMISSIONS - {Permission.DELETE_CUSTOMER},
    Role.MANAGER: {
        Permission.READ_CUSTOMERS,
        Permission.CREATE_CUSTOMER,
        Permission.UPDATE_CUSTOMER,
        Permission.READ_JOBS,
        Permission.CREATE_JOB,
        Permission.SCHEDULE_JOB,
        Permission.CREATE_INVOICE,
        Permission.SEND_INVOICE,
        Permission.VIEW_FINANCIALS,
        Permission.RUN_MARKETING,
        Permission.SEND_CUSTOMER_MESSAGE,
        Permission.EXECUTE_AI_ACTION,
        Permission.READ_LEADS,
        Permission.CREATE_LEAD,
        Permission.UPDATE_LEAD,
        Permission.QUALIFY_LEAD,
        Permission.READ_APPOINTMENTS,
        Permission.CREATE_APPOINTMENT,
        Permission.CANCEL_APPOINTMENT,
    },
    Role.STAFF: {
        Permission.READ_CUSTOMERS,
        Permission.CREATE_CUSTOMER,
        Permission.UPDATE_CUSTOMER,
        Permission.READ_JOBS,
        Permission.CREATE_JOB,
        Permission.SCHEDULE_JOB,
        Permission.SEND_CUSTOMER_MESSAGE,
        Permission.READ_LEADS,
        Permission.CREATE_LEAD,
        Permission.UPDATE_LEAD,
        Permission.READ_APPOINTMENTS,
        Permission.CREATE_APPOINTMENT,
        Permission.CANCEL_APPOINTMENT,
    },
    Role.TECHNICIAN: {
        Permission.READ_JOBS,
        Permission.SCHEDULE_JOB,
        Permission.READ_CUSTOMERS,
        Permission.READ_LEADS,
        Permission.READ_APPOINTMENTS,
    },
    Role.ACCOUNTANT: {
        Permission.VIEW_FINANCIALS,
        Permission.CREATE_INVOICE,
        Permission.SEND_INVOICE,
        Permission.COLLECT_PAYMENT,
        Permission.READ_CUSTOMERS,
        Permission.READ_LEADS,
    },
    Role.READ_ONLY: {
        Permission.READ_CUSTOMERS,
        Permission.READ_JOBS,
        Permission.VIEW_FINANCIALS,
        Permission.READ_LEADS,
        Permission.READ_APPOINTMENTS,
    },
}


def role_has_permission(role: Role, permission: Permission) -> bool:
    return permission in ROLE_PERMISSIONS.get(role, set())
