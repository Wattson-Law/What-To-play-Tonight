import unittest
from coop_night_picker.probe import build_url, classify_status

class ProbeHelpersTest(unittest.TestCase):
    def test_build_url(self):
        self.assertEqual(build_url("https://example.test/", "/search/results"), "https://example.test/search/results")
    def test_redirect_classification(self):
        for code in (301, 302, 303, 307, 308):
            self.assertEqual(classify_status(code), "redirect_trap")
        self.assertEqual(classify_status(200), "ok")
        self.assertEqual(classify_status(500), "http_error")

    def test_combination_failed_for_non_2xx(self):
        from coop_night_picker.probe import combination_failed
        self.assertFalse(combination_failed({"probes": [{"status": 200}]}))
        self.assertTrue(combination_failed({"probes": [{"status": 302}]}))
        self.assertTrue(combination_failed({"probes": [{"status": 500}]}))

if __name__ == "__main__": unittest.main()
