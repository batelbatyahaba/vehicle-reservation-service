class DomainError(Exception):
    status_code = 400
    code = "domain_error"


class NotFoundError(DomainError):
    status_code = 404


class ConflictError(DomainError):
    status_code = 409


class UnprocessableError(DomainError):
    status_code = 422


class VehicleNotFound(NotFoundError):
    code = "vehicle_not_found"


class ReservationNotFound(NotFoundError):
    code = "reservation_not_found"


class DuplicatePlateNumber(ConflictError):
    code = "duplicate_plate_number"


class VehicleInMaintenance(ConflictError):
    code = "vehicle_in_maintenance"


class VehicleUnavailable(ConflictError):
    code = "vehicle_unavailable"


class InvalidStateTransition(ConflictError):
    code = "invalid_state_transition"


class InvalidDateRange(UnprocessableError):
    code = "invalid_date_range"


class StartDateInPast(UnprocessableError):
    code = "start_date_in_past"


class IdempotencyKeyReused(UnprocessableError):
    code = "idempotency_key_reused"
