import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch
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

    def test_default_probe_runs_proxy_combinations_and_prints_fallback_guidance(self):
        from coop_night_picker import probe

        def fake_run(name, host, proxy):
            return {
                "combination": name,
                "proxy": proxy,
                "host": host,
                "probes": [
                    {
                        "url": f"{host}/appreviews/548430",
                        "status": None if name.startswith("C") else 302,
                        "classification": "error" if name.startswith("C") else "redirect_trap",
                    }
                ],
            }

        output = io.StringIO()
        with patch.object(probe, "run_combination", side_effect=fake_run) as run, redirect_stdout(output):
            self.assertEqual(probe.main([]), 0)

        self.assertEqual(run.call_count, 4)
        text = output.getvalue()
        self.assertIn("DOMAIN-SUFFIX,steampowered.com,PROXY", text)
        self.assertIn("DOMAIN-SUFFIX,steamcommunity.com,PROXY", text)
        self.assertIn("Fallback Mode", text)

if __name__ == "__main__": unittest.main()
