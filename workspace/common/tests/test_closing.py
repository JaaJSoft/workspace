from unittest.mock import MagicMock

from django.test import SimpleTestCase

from workspace.common.closing import close_all


class CloseAllTests(SimpleTestCase):
    def test_closes_every_handle(self):
        handles = [MagicMock(), MagicMock(), MagicMock()]

        close_all(handles)

        for h in handles:
            h.close.assert_called_once()

    def test_failing_close_does_not_leak_the_rest(self):
        first = MagicMock()
        bad = MagicMock()
        bad.close.side_effect = OSError('already gone')
        last = MagicMock()

        close_all([first, bad, last])

        first.close.assert_called_once()
        last.close.assert_called_once()

    def test_empty_iterable_is_a_noop(self):
        close_all([])
