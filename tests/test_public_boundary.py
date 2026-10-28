import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bin'))
from operator_engine import fetch_release


class PublicBoundaryTest(unittest.TestCase):
    def test_default_fetcher_refuses_without_network(self):
        with patch('urllib.request.build_opener') as opener:
            with self.assertRaisesRegex(ValueError, 'No default release feed'):
                fetch_release()
            opener.assert_not_called()


if __name__ == '__main__':
    unittest.main()
