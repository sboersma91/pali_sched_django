from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from members.models import Organization, OrganizationMembership

from .demo_session_provisioning import provision_clean_demo_session


class AccountIndicatorTests(TestCase):
    def make_member(self, username, organization):
        user = get_user_model().objects.create_user(username=username)
        OrganizationMembership.objects.create(
            user=user,
            organization=organization,
        )
        return user

    def test_anonymous_visitor_sees_signed_out_state_and_login(self):
        response = self.client.get(reverse('demo-landing'))

        self.assertContains(response, 'Not signed in')
        self.assertContains(response, f'href="{reverse("login")}"')
        self.assertNotContains(response, '<strong>Organization:</strong>')

    def test_temporary_demo_shows_account_organization_and_exit_demo(self):
        result = provision_clean_demo_session()
        self.client.force_login(result.user)

        response = self.client.get(reverse('home-paid'))

        self.assertContains(response, result.user.get_username())
        self.assertContains(response, result.organization.name)
        self.assertContains(response, 'Temporary demo')
        self.assertContains(response, f'action="{reverse("demo-exit")}"')
        self.assertContains(response, 'Exit demo')
        self.assertNotContains(
            response,
            f'action="{reverse("logout")}"',
        )

    def test_normal_user_sees_customer_context_and_logout(self):
        organization = Organization.objects.create(
            name='Northwind Programs',
            purpose=Organization.Purpose.CUSTOMER,
        )
        user = self.make_member('northwind-user', organization)
        self.client.force_login(user)

        response = self.client.get(reverse('home-paid'))

        self.assertContains(response, 'northwind-user')
        self.assertContains(response, 'Northwind Programs')
        self.assertContains(response, 'Customer organization')
        self.assertContains(response, f'action="{reverse("logout")}"')
        self.assertNotContains(response, 'Exit demo')

    def test_purpose_label_uses_stored_state_not_name(self):
        organization = Organization.objects.create(
            name='Temporary Demo Looking Customer',
            purpose=Organization.Purpose.CANONICAL_DEMO,
        )
        user = self.make_member('canonical-user', organization)
        self.client.force_login(user)

        response = self.client.get(reverse('home-paid'))

        self.assertContains(response, 'Canonical/default organization')
        self.assertNotContains(response, 'Temporary demo')
        self.assertContains(response, f'action="{reverse("logout")}"')

    def test_indicator_never_exposes_another_organization(self):
        own = Organization.objects.create(name='Visible Organization')
        foreign = Organization.objects.create(name='Secret Foreign Organization')
        user = self.make_member('isolated-user', own)
        self.make_member('foreign-user', foreign)
        self.client.force_login(user)

        response = self.client.get(reverse('home-paid'))

        self.assertContains(response, own.name)
        self.assertNotContains(response, foreign.name)

    def test_authenticated_user_without_membership_fails_neutral(self):
        user = get_user_model().objects.create_user(username='membership-missing')
        self.client.force_login(user)

        response = self.client.get(reverse('home'))

        self.assertContains(response, 'membership-missing')
        self.assertContains(response, 'No active organization')
        self.assertContains(response, f'action="{reverse("logout")}"')
        self.assertNotContains(response, '<strong>Organization:</strong>')
