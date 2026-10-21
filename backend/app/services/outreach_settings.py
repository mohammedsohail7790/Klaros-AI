"""Validation models for clinic outreach campaign settings (pure, no I/O)."""

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


def _strip_nonblank(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("must not be blank")
    return value


class TermSheet(BaseModel):
    basis: Literal["PERCENTAGE", "FLAT"]
    min_value: Decimal
    max_value: Decimal
    payment_days: int = Field(ge=1, le=120)
    currency: str
    qualifying_outcome: str

    @field_validator("currency", mode="before")
    @classmethod
    def _currency(cls, value):
        if not isinstance(value, str):
            raise ValueError("currency must be a string")
        value = value.strip()
        if len(value) != 3 or not value.isascii() or not value.isalpha():
            raise ValueError("currency must be exactly three letters")
        return value.upper()

    @field_validator("qualifying_outcome", mode="before")
    @classmethod
    def _outcome(cls, value):
        if not isinstance(value, str):
            raise ValueError("must be a string")
        return _strip_nonblank(value)

    @model_validator(mode="after")
    def _check_range(self):
        if self.min_value <= 0 or self.max_value <= 0:
            raise ValueError("values must be greater than 0")
        if self.min_value > self.max_value:
            raise ValueError("min_value must not exceed max_value")
        if self.basis == "PERCENTAGE" and self.max_value > 100:
            raise ValueError("percentage must not exceed 100")
        return self


class Sender(BaseModel):
    display_name: str
    signature: str
    company_name: str
    postal_address: str

    @field_validator(
        "display_name", "signature", "company_name", "postal_address", mode="before"
    )
    @classmethod
    def _nonblank(cls, value):
        if not isinstance(value, str):
            raise ValueError("must be a string")
        return _strip_nonblank(value)


class Limits(BaseModel):
    daily_cap: int = Field(default=20, ge=1)
    ramp_start: int = Field(default=5, ge=1)
    max_emails_per_clinic: int = Field(default=3, ge=1, le=3)
    send_window_start: int = Field(default=9, ge=0, le=23)
    send_window_end: int = Field(default=17, ge=1, le=24)
    weekdays_only: bool = True

    @model_validator(mode="after")
    def _check_limits(self):
        if self.ramp_start > self.daily_cap:
            raise ValueError("ramp_start must not exceed daily_cap")
        if self.send_window_start >= self.send_window_end:
            raise ValueError("send window must start before it ends")
        return self


class OutreachConfig(BaseModel):
    term_sheet: TermSheet
    sender: Sender
    limits: Limits = Field(default_factory=Limits)
    countries: list[str]

    @field_validator("countries", mode="before")
    @classmethod
    def _countries(cls, value):
        if not isinstance(value, list) or not value:
            raise ValueError("countries must be a non-empty list")
        out = []
        for item in value:
            if not isinstance(item, str):
                raise ValueError("country codes must be strings")
            code = item.strip()
            if len(code) != 2 or not code.isascii() or not code.isalpha():
                raise ValueError("country codes must be two letters")
            out.append(code.upper())
        return out
