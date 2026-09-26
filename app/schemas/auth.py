from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class SignupRequest(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=8, max_length=72)

    @field_validator("email")
    @classmethod
    def lower_email(cls, v: str) -> str:
        return v.lower()

    @field_validator("full_name")
    @classmethod
    def strip_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("full_name cannot be blank")
        return v

    @field_validator("password")
    @classmethod
    def check_password(cls, v: str) -> str:
        # bcrypt only looks at the first 72 bytes
        if len(v.encode()) > 72:
            raise ValueError("password is too long")
        if v.isdigit() or v.isalpha():
            raise ValueError("password must contain both letters and numbers")
        return v


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=72)

    @field_validator("email")
    @classmethod
    def lower_email(cls, v: str) -> str:
        return v.lower()


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    full_name: str
    is_admin: bool
