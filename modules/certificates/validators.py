from base.validators import validate_with_schema
from modules.certificates.schemas import (
    BoxMappingSchema,
    CSVColumnsSchema,
    NamingConventionSchema,
    RowDataSchema,
)

# Named (not lambda) so Django migrations can serialize a reference to them.


def validate_csv_columns(value):
    validate_with_schema(CSVColumnsSchema, value)


def validate_mapping(value):
    validate_with_schema(BoxMappingSchema, value)


def validate_row_data(value):
    validate_with_schema(RowDataSchema, value)


def validate_naming_convention(value):
    if value is not None:
        validate_with_schema(NamingConventionSchema, value)
