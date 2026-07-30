import unittest
import os
import json
from unittest.mock import patch
import run_performance_benchmarks

class TestBenchmarks(unittest.TestCase):
    def test_calculate_percentiles(self):
        times = [1.0, 2.0, 3.0, 4.0, 5.0]
        # With 5 items, quantiles calculation is roughly:
        p50, p95 = run_performance_benchmarks.calculate_percentiles(times)
        self.assertTrue(p50 > 0)
        self.assertTrue(p95 > 0)
        
    def test_calculate_percentiles_empty(self):
        p50, p95 = run_performance_benchmarks.calculate_percentiles([])
        self.assertEqual(p50, 0)
        self.assertEqual(p95, 0)

    @patch('run_performance_benchmarks.subprocess.run')
    def test_measure_execution_time(self, mock_run):
        times = run_performance_benchmarks.measure_execution_time(['echo', 'test'], iterations=2)
        self.assertEqual(len(times), 2)
        self.assertTrue(all(isinstance(t, float) for t in times))

    def test_generate_reports(self):
        results = {
            'test_metric': {'p50_seconds': 0.1, 'p95_seconds': 0.2}
        }
        json_path = 'test_report.json'
        md_path = 'test_report.md'
        
        try:
            run_performance_benchmarks.generate_reports(results, json_path, md_path)
            
            self.assertTrue(os.path.exists(json_path))
            self.assertTrue(os.path.exists(md_path))
            
            with open(json_path, 'r') as f:
                data = json.load(f)
                self.assertIn('test_metric', data)
                
            with open(md_path, 'r') as f:
                content = f.read()
                self.assertIn('test_metric', content.lower())
                self.assertIn('0.1000', content)
        finally:
            if os.path.exists(json_path):
                os.remove(json_path)
            if os.path.exists(md_path):
                os.remove(md_path)

if __name__ == '__main__':
    unittest.main()
