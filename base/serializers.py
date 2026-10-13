from typing import List, Union
from rest_framework import serializers

from base.model import ModelBase
from base.constants import FIELDS_MAPPING
from base.functions import getattr_recursive
from base.types import CascaderType, DynamicKeysType

# Write your serializers here


class BaseSerializer(serializers.ModelSerializer):

    file_fields: List[Union[str, CascaderType]] = []
    dynamic_keys: List[Union[str, DynamicKeysType]] = []

    def __init__(self, *args, **kwargs):
        fields = kwargs.pop("fields", None)
        exclude = kwargs.pop("exclude", None)

        if fields is not None:
            allowed = set(fields).union(
                set(fields).intersection(set(ModelBase.BASE_MODEL_FIELDS))
            )
            existing = set(self.fields.keys())

            difference = existing.difference(allowed)

            for each in difference:
                self.fields.pop(each)
        elif exclude is not None:
            trash = set(exclude).union(set(ModelBase.BASE_MODEL_FIELDS))
            for each in trash:
                self.fields.pop(each)
        else:
            pass

        super(BaseSerializer, self).__init__(*args, **kwargs)

        for field in self.dynamic_keys:
            isDict = isinstance(field, dict)
            field_type = (
                (
                    "int"
                    if field.get("source").split(".")[-1] == "id"
                    # or field.get("type") == "int"
                    else field.get("type", "str")
                )
                if isDict
                else "str"
            )

            self.fields.update(
                {
                    field.get("name") if isDict else field.replace(".", "_"): (
                        getattr(
                            serializers, FIELDS_MAPPING.get(field_type, "CharField")
                        )
                    )(
                        source=field.get("source") if isDict else field,
                        read_only=True,
                    )
                }
            )

    def to_representation(self, instance):
        fields = super().to_representation(instance)
        request = self.context.get("request")
        for field in self.file_fields:
            isStr = isinstance(field, str)
            field_name = field if isStr else field.get("name")
            if field_name in fields.keys():
                file_obj = getattr_recursive(
                    instance, field if isStr else field.get("field")
                )
                if file_obj:
                    url = file_obj.get_url()
                    fields[field_name] = {
                        "url": request.build_absolute_uri(url) if request else url,
                        "uuid": file_obj.uuid,
                    }
                else:
                    fields[field_name] = None
        return fields


class FileObjectSerializer(serializers.Serializer):
    url = serializers.SerializerMethodField()
    uuid = serializers.CharField()

    def get_url(self, obj) -> str:
        return obj.get_url()
