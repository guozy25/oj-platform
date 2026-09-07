from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CurrentUser:
    user_id: str
    username: str
    role: str
    session_id: str
