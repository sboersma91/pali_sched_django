from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from .models import Organization, OrganizationMembership


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


class OrganizationModelTests(TestCase):
    def test_organization_can_be_created(self):
        organization = Organization.objects.create(name="Camp Operations")

        self.assertEqual(str(organization), "Camp Operations")
        self.assertIsNotNone(organization.created_at)
        self.assertIsNotNone(organization.updated_at)

    def test_user_can_be_connected_to_organization(self):
        user = get_user_model().objects.create_user(username="operator")
        organization = Organization.objects.create(name="Camp Operations")

        membership = OrganizationMembership.objects.create(
            user=user,
            organization=organization,
        )

        self.assertEqual(membership.user, user)
        self.assertEqual(membership.organization, organization)
        self.assertEqual(user.organization_membership.organization, organization)

    def test_membership_initially_enforces_one_organization_per_user(self):
        user = get_user_model().objects.create_user(username="operator")
        first_organization = Organization.objects.create(name="First Organization")
        second_organization = Organization.objects.create(name="Second Organization")
        OrganizationMembership.objects.create(user=user, organization=first_organization)

        with self.assertRaises(IntegrityError):
            OrganizationMembership.objects.create(user=user, organization=second_organization)
