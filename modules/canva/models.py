from django.conf import settings
from django.db import models
from django.db.models import Q

from base.defaults import DEFAULT_ON_DELETE
from base.enums import Status
from base.model import ModelBase

# Create your models here.


class CanvaConnection(ModelBase):
    "A user's Canva Connect OAuth grant. Tokens are Fernet-encrypted; only CanvaService reads or writes them."

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=DEFAULT_ON_DELETE, related_name="canva_connections")
    access_token_enc = models.TextField()
    refresh_token_enc = models.TextField()
    expires_at = models.DateTimeField()
    scope = models.CharField(max_length=500, blank=True, default="")

    class Meta(ModelBase.Meta):
        constraints = [
            models.UniqueConstraint(fields=["user"], condition=Q(status__in=[Status.CREATED, Status.UPDATED]), name="unique_active_canva_connection_per_user"),
        ]
