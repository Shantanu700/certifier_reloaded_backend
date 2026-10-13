from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field, RootModel


class CSVColumnsSchema(RootModel[Annotated[list[str], Field(min_length=1)]]):
    "CertificateBatch.csv_columns -- the parsed CSV header row; must be non-empty."


class BoxMappingSchema(RootModel[dict[str, str]]):
    "CertificateBatch.mapping -- {box_key: csv_column}."


class RowDataSchema(RootModel[dict[str, str]]):
    "GeneratedCertificate.row_data -- one CSV row, values already normalized to str (never None)."


class CSVKeyPart(BaseModel):
    type: Literal["csv_key"]
    key: str


class SequencePart(BaseModel):
    type: Literal["sequence"]
    start: int = 1
    step: int = 1


class RandomPart(BaseModel):
    type: Literal["random"]
    kind: Literal["string", "number"] = "string"
    length: Annotated[int, Field(ge=1, le=32)] = 6


class FixedPart(BaseModel):
    type: Literal["fixed"]
    value: str


NamingPart = Annotated[Union[CSVKeyPart, SequencePart, RandomPart, FixedPart], Field(discriminator="type")]


class NamingConventionSchema(RootModel[Annotated[list[NamingPart], Field(min_length=1)]]):
    "CertificateBatch.naming_convention -- ordered parts concatenated to build each generated file's base name."
