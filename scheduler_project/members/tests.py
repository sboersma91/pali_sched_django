from unittest.mock import patch

from django.contrib.auth.models import AnonymousUser
from django.http import HttpResponseRedirect
from django.test import SimpleTestCase, RequestFactory

from members.views import login_user


class LoginRedirectTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    @patch('members.views.login')
    @patch('members.views.authenticate')
    def test_login_success_redirects_to_home_paid_route(self, mock_authenticate, mock_login):
        mock_authenticate.return_value = object()
        request = self.factory.post('/members/login_user', {
            'username': 'demo',
            'password': 'demo',
        })
        request.user = AnonymousUser()

        response = login_user(request)

        self.assertIsInstance(response, HttpResponseRedirect)
        self.assertEqual(response.url, '/home_paid')
