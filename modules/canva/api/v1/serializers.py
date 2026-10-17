from rest_framework import serializers

# Write your serializers here


class CanvaAuthorizeUrlSerializer(serializers.Serializer):
    authorize_url = serializers.URLField(help_text="Send the browser here to start the Canva sign-in.")


class CanvaCallbackSerializer(serializers.Serializer):
    code = serializers.CharField()
    state = serializers.CharField()


class CanvaStatusSerializer(serializers.Serializer):
    connected = serializers.BooleanField()


class CanvaDesignSerializer(serializers.Serializer):
    id = serializers.CharField()
    title = serializers.CharField()
    thumbnail_url = serializers.URLField(allow_null=True, help_text="Expires after 15 minutes; don't cache it.")
    thumbnail_width = serializers.IntegerField(allow_null=True, help_text="Same proportions as the design; use it for the aspect ratio.")
    thumbnail_height = serializers.IntegerField(allow_null=True)
    page_count = serializers.IntegerField(allow_null=True)
    updated_at = serializers.IntegerField(allow_null=True, help_text="Unix timestamp (seconds).")


class CanvaDesignPageSerializer(serializers.Serializer):
    items = CanvaDesignSerializer(many=True)
    continuation = serializers.CharField(allow_null=True, help_text="Pass back as ?continuation= for the next page.")
