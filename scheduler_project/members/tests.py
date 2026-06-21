from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse


@override_settings(ALLOWED_HOSTS=["localhost", "testserver"])
class BuiltInLoginRedirectTests(TestCase):
    def setUp(self):
        self.client = Client(HTTP_HOST="localhost")
        get_user_model().objects.create_user(username="demo", password="demo-password")

    def test_login_success_redirects_to_operational_dashboard(self):
        response = self.client.post(
            reverse("login"),
            {"username": "demo", "password": "demo-password"},
        )

        self.assertRedirects(response, reverse("home-paid"))
