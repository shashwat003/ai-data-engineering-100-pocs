from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

TOL = 0.011


class LineItem(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    description: str = Field(min_length=1, max_length=80)
    qty: int = Field(gt=0, lt=10_000)
    unit_price: float = Field(ge=0, lt=1e7)


class Invoice(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    vendor: str = Field(min_length=1, max_length=80)
    invoice_number: str = Field(pattern=r"^INV-\d{4,8}$")
    invoice_date: date
    currency: Literal["USD", "EUR", "GBP"]
    line_items: list[LineItem] = Field(min_length=1, max_length=50)
    subtotal: float = Field(ge=0)
    tax: float = Field(ge=0)
    total: float = Field(ge=0)

    @model_validator(mode="after")
    def arithmetic_consistent(self):
        s = sum(x.qty * x.unit_price for x in self.line_items)
        if abs(s - self.subtotal) > TOL * max(1, len(self.line_items)):
            raise ValueError(f"subtotal {self.subtotal} != sum of line items {s:.2f}")
        if abs(self.subtotal + self.tax - self.total) > TOL:
            raise ValueError(f"total {self.total} != subtotal + tax {self.subtotal + self.tax:.2f}")
        return self
