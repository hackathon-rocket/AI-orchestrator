"""Model-facing constraints; rejected proposals still reach deterministic validation."""

from decimal import Decimal
from typing import Annotated

from pydantic import Field, WithJsonSchema

from app.schemas.extraction import ExtractionData, Fact


class ClassificationProposal(Fact):
    # Constrain generation without turning a bad classification into a parse error
    # that loses otherwise useful facts. The validator still checks every proposal,
    # including legacy/localized values from compatible providers.
    value: Annotated[
        str | Decimal | None,
        WithJsonSchema(
            {
                "anyOf": [
                    {
                        "type": "string",
                        "enum": ["invoice", "receipt", "contract", "bank_statement"],
                    },
                    {"type": "null"},
                ]
            }
        ),
    ] = None


class ExtractionProposal(ExtractionData):
    classification: ClassificationProposal = Field(
        default_factory=ClassificationProposal,
        description=ExtractionData.model_fields["classification"].description,
    )
