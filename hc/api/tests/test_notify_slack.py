# coding: utf-8

from datetime import timedelta as td
import json
from unittest.mock import patch

from django.utils.timezone import now
from hc.api.models import Channel, Check, Notification, Ping
from hc.test import BaseTestCase
from requests.exceptions import Timeout
from django.test.utils import override_settings


class NotifySlackTestCase(BaseTestCase):
    def _setup_data(self, value, status="down", email_verified=True):
        self.check = Check(project=self.project)
        self.check.name = "Foobar"
        self.check.status = status
        self.check.last_ping = now() - td(minutes=61)
        self.check.save()

        self.ping = Ping(owner=self.check)
        self.ping.created = now() - td(minutes=61)
        self.ping.save()

        self.channel = Channel(project=self.project)
        self.channel.kind = "slack"
        self.channel.value = value
        self.channel.email_verified = email_verified
        self.channel.save()
        self.channel.checks.add(self.check)

    @override_settings(SITE_ROOT="http://testserver")
    @patch("hc.api.transports.requests.request")
    def test_it_works(self, mock_post):
        self._setup_data("https://example.org")
        mock_post.return_value.status_code = 200

        self.channel.notify(self.check)
        assert Notification.objects.count() == 1

        args, kwargs = mock_post.call_args
        self.assertEqual(args[1], "https://example.org")

        payload = kwargs["json"]
        attachment = payload["attachments"][0]
        fields = {f["title"]: f["value"] for f in attachment["fields"]}
        self.assertEqual(fields["Last Ping"], "Success, an hour ago")

        # The payload should not contain check's code
        serialized = json.dumps(payload)
        self.assertNotIn(str(self.check.code), serialized)
        self.assertIn("http://testserver/static/img/logo.png", serialized)

    @patch("hc.api.transports.requests.request")
    def test_slack_with_complex_value(self, mock_post):
        v = json.dumps({"incoming_webhook": {"url": "123"}})
        self._setup_data(v)
        mock_post.return_value.status_code = 200

        self.channel.notify(self.check)
        assert Notification.objects.count() == 1

        args, kwargs = mock_post.call_args
        self.assertEqual(args[1], "123")

    @patch("hc.api.transports.requests.request")
    def test_it_handles_500(self, mock_post):
        self._setup_data("123")
        mock_post.return_value.status_code = 500

        self.channel.notify(self.check)

        n = Notification.objects.get()
        self.assertEqual(n.error, "Received status code 500")

    @patch("hc.api.transports.requests.request", side_effect=Timeout)
    def test_it_handles_timeout(self, mock_post):
        self._setup_data("123")

        self.channel.notify(self.check)

        n = Notification.objects.get()
        self.assertEqual(n.error, "Connection timed out")
        # Expect 1 try and 2 retries:
        self.assertEqual(mock_post.call_count, 3)

    @patch("hc.api.transports.requests.request")
    def test_slack_with_tabs_in_schedule(self, mock_post):
        self._setup_data("123")
        self.check.kind = "cron"
        self.check.schedule = "*\t* * * *"
        self.check.save()
        mock_post.return_value.status_code = 200

        self.channel.notify(self.check)
        self.assertEqual(Notification.objects.count(), 1)
        self.assertTrue(mock_post.called)

    @override_settings(SLACK_ENABLED=False)
    def test_it_requires_slack_enabled(self):
        self._setup_data("123")
        self.channel.notify(self.check)

        n = Notification.objects.get()
        self.assertEqual(n.error, "Slack notifications are not enabled.")

    @patch("hc.api.transports.requests.request")
    def test_it_does_not_retry_404(self, mock_post):
        self._setup_data("123")
        mock_post.return_value.status_code = 404

        self.channel.notify(self.check)

        n = Notification.objects.get()
        self.assertEqual(n.error, "Received status code 404")
        self.assertEqual(mock_post.call_count, 1)

    @patch("hc.api.transports.requests.request")
    def test_it_disables_channel_on_404(self, mock_post):
        self._setup_data("123")
        mock_post.return_value.status_code = 404

        self.channel.notify(self.check)
        self.channel.refresh_from_db()
        self.assertTrue(self.channel.disabled)

    @override_settings(SITE_ROOT="http://testserver")
    @patch("hc.api.transports.requests.request")
    def test_it_handles_last_ping_fail(self, mock_post):
        self._setup_data("123")
        mock_post.return_value.status_code = 200

        self.ping.kind = "fail"
        self.ping.save()

        self.channel.notify(self.check)
        assert Notification.objects.count() == 1

        args, kwargs = mock_post.call_args
        attachment = kwargs["json"]["attachments"][0]
        fields = {f["title"]: f["value"] for f in attachment["fields"]}
        self.assertEqual(fields["Last Ping"], "Failure, an hour ago")

    @override_settings(SITE_ROOT="http://testserver")
    @patch("hc.api.transports.requests.request")
    def test_it_shows_ignored_nonzero_exitstatus(self, mock_post):
        self._setup_data("123")
        mock_post.return_value.status_code = 200

        self.ping.kind = "ign"
        self.ping.exitstatus = 123
        self.ping.save()

        self.channel.notify(self.check)
        assert Notification.objects.count() == 1

        args, kwargs = mock_post.call_args
        attachment = kwargs["json"]["attachments"][0]
        fields = {f["title"]: f["value"] for f in attachment["fields"]}
        self.assertEqual(fields["Last Ping"], "Ignored, an hour ago")
