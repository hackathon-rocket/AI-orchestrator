"""Typed, request-scoped mapping configuration supplied by the trusted Laravel backend."""

from typing import Literal

from pydantic import Field, model_validator

from app.schemas.common import Model
from app.schemas.extraction import UniversalFields


class MappingRule(Model):
    source: str = Field(min_length=1, max_length=100)
    destination: str = Field(
        min_length=1, max_length=500, pattern=r"^[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)*$"
    )
    value_map: dict[str, str] | None = Field(default=None, max_length=200)
    required_currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    review_warning: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def valid_source(self):
        if self.source not in UniversalFields.model_fields and self.source != "classification":
            raise ValueError("Unknown extraction source")
        if self.value_map and any(len(k) > 200 or len(v) > 200 for k, v in self.value_map.items()):
            raise ValueError("Oversized select mapping")
        return self


class InlineMapping(Model):
    id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
    form_id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
    version: str | None = Field(default=None, max_length=100)
    document_types: list[Literal["invoice", "receipt", "contract", "bank_statement"]] = Field(
        default_factory=lambda: ["invoice"], min_length=1, max_length=4
    )
    rules: list[MappingRule] = Field(min_length=1, max_length=200)
    reserved: list[str] = Field(default_factory=list, max_length=200)

    @model_validator(mode="after")
    def valid_rules(self):
        destinations = [r.destination for r in self.rules]
        if len(destinations) != len(set(destinations)) or len(self.reserved) != len(
            set(self.reserved)
        ):
            raise ValueError("Duplicate destination")
        if set(destinations) & set(self.reserved):
            raise ValueError("Cannot fill reserved fields")
        return self
