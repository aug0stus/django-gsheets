from django.db import models


class AccessCredentials(models.Model):
    token = models.CharField(max_length=255)
    refresh_token = models.CharField(max_length=255)
    token_uri = models.CharField(max_length=255)
    client_id = models.CharField(max_length=255)
    client_secret = models.CharField(max_length=255)
    scopes = models.CharField(max_length=255)

    created_time = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f'{self.token} // {self.refresh_token} ({self.id})'
