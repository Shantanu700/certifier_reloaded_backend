from modules.core.models import MasterDropdown, User
from base.serializers import BaseSerializer

# Write your serializers here


class DropDownSerializer(BaseSerializer):
    class Meta:
        model = MasterDropdown
        fields = "__all__"


class LoginSerializer(BaseSerializer):
    file_fields = ["profile_image"]

    class Meta:
        model = User
        exclude = User.USER_MODEL_FIELDS + ("password",)
