from importlib import import_module

from django.core.management import call_command
from django.test import SimpleTestCase
from django.urls import reverse


class UrlConfigImportTests(SimpleTestCase):
    def test_project_urlconf_imports(self):
        module = import_module('scheduler_project.urls')
        self.assertTrue(hasattr(module, 'urlpatterns'))

    def test_scheduler_app_urlconf_imports(self):
        module = import_module('scheduler_app.urls')
        self.assertTrue(hasattr(module, 'urlpatterns'))

    def test_members_urlconf_imports(self):
        module = import_module('members.urls')
        self.assertTrue(hasattr(module, 'urlpatterns'))


class UrlReverseSmokeTests(SimpleTestCase):
    def test_can_reverse_stable_named_urls(self):
        self.assertEqual(reverse('home'), '/')
        self.assertEqual(reverse('home-paid'), '/home_paid')
        self.assertEqual(reverse('add-course'), '/add_course')
        self.assertEqual(reverse('login'), '/members/login_user')
        self.assertEqual(reverse('logout'), '/members/logout_user')
        self.assertEqual(reverse('register_user'), '/members/register_user')


class DjangoCheckSmokeTests(SimpleTestCase):
    def test_django_checks_pass(self):
        call_command('check')
