from pydantic import BaseModel


class AuthenticatedContext(BaseModel):
    """Trusted user and organization identity resolved at the API boundary."""

    user_id: str
    organization_id: str
