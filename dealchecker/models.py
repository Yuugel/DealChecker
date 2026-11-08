"""Validated wire and adapter models for the price monitor."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator, model_validator


class WatchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    kind: Literal["exact", "discovery"] = "exact"
    query: str = Field(default="", max_length=300)
    source_ids: list[str] = Field(default_factory=lambda: ["apple"], max_length=20)
    product_id: str = Field(default="", max_length=200)
    url: str = Field(default="", max_length=1000)
    gtin: str = Field(default="", max_length=100)
    model: str = Field(default="", max_length=200)
    variant: str = Field(default="", max_length=200)
    synonyms: list[str] = Field(default_factory=list, max_length=20)
    category: str = Field(default="", max_length=200)
    manufacturer: str = Field(default="", max_length=200)
    exclusions: list[str] = Field(default_factory=list, max_length=20)
    merchants_include: list[str] = Field(default_factory=list, max_length=20)
    merchants_exclude: list[str] = Field(default_factory=list, max_length=20)
    target_cents: int | None = Field(default=None, ge=0)
    max_price_cents: int | None = Field(default=None, ge=0)
    priority: Literal["critical", "high", "normal", "low"] = "normal"
    interval_seconds: int | None = Field(default=None, ge=300, le=86400)
    paused: bool = False

    @field_validator("name", "query", "product_id", "url", "gtin", "model", "variant", "category", "manufacturer", mode="before")
    @classmethod
    def trim(cls, value):
        return value.strip() if isinstance(value, str) else value

    @field_validator("source_ids", "synonyms", "exclusions", "merchants_include", "merchants_exclude")
    @classmethod
    def clean_list(cls, value):
        cleaned = [item.strip() for item in value]
        if any(not item or len(item) > 200 for item in cleaned):
            raise ValueError("list entries must be 1–200 characters")
        return list(dict.fromkeys(cleaned))

    @model_validator(mode="after")
    def required_identity(self):
        if not self.source_ids:
            raise ValueError("at least one source is required")
        if not any((self.query, self.product_id, self.url, self.gtin, self.model)):
            raise ValueError("a query or product identity is required")
        if self.kind == "discovery" and not self.query:
            raise ValueError("discovery requires query")
        return self


class Watch(WatchInput):
    id: int
    next_due: float
    failure_count: int = 0
    last_error: str | None = None
    last_checked: float | None = None
    running: bool = False


class Offer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str = Field(min_length=1, max_length=100)
    merchant: str = Field(min_length=1, max_length=200)
    product_id: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=300)
    url: str = Field(min_length=1, max_length=1000)
    price_cents: int = Field(ge=0)
    shipping_cents: int | None = Field(ge=0)
    currency: Literal["EUR"] = "EUR"
    availability: Literal["in_stock", "out_of_stock", "unknown"]
    checked_at: float
    variant: str = ""
    gtin: str = ""
    model: str = ""
    manufacturer: str = ""
    category: str = ""
    coupon: str | None = None
    unit_price: str | None = None
    quality: str = "source_reported"
    provenance: str = "live"

    @computed_field
    @property
    def total_cents(self) -> int | None:
        return None if self.shipping_cents is None else self.price_cents + self.shipping_cents

    @computed_field
    @property
    def key(self) -> str:
        return "\x1f".join((self.source.casefold(), self.product_id.casefold(), self.variant.casefold()))
