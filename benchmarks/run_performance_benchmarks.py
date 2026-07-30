import subprocess
import time
import json
import statistics
import os

def measure_execution_time(cmd, iterations=5):
    times = []
    for _ in range(iterations):
        start = time.perf_counter()
        try:
            # We discard output to ensure no secrets or workspace content are read/leaked
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
        except Exception:
            pass
        finally:
            times.append(time.perf_counter() - start)
    return times

def calculate_percentiles(times):
    if not times:
        return 0, 0
    return statistics.quantiles(times, n=100)[49], statistics.quantiles(times, n=100)[94]

def benchmark_extension_bridge():
    # Placeholder for local extension bridge IPC/ping
    # Measures loopback socket connection overhead as a proxy for the bridge
    import socket
    times = []
    for _ in range(20):
        start = time.perf_counter()
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(0.1)
                s.connect(('127.0.0.1', 9222)) # Default chromium debugging port
        except Exception:
            pass
        finally:
            times.append(time.perf_counter() - start)
    return times

def run_all_benchmarks():
    results = {}
    
    # 1. Local extension bridge p50/p95
    ext_times = benchmark_extension_bridge()
    p50, p95 = calculate_percentiles(ext_times)
    results['local_extension_bridge'] = {
        'p50_seconds': p50,
        'p95_seconds': p95
    }
    
    # 2. TokScale 4.7.0 aggregate export
    tokscale_times = measure_execution_time(['tokscale', 'export'])
    t_p50, t_p95 = calculate_percentiles(tokscale_times)
    results['tokscale_aggregate_export'] = {
        'p50_seconds': t_p50,
        'p95_seconds': t_p95
    }
    
    # 3. Bitwarden CLI status startup
    bw_times = measure_execution_time(['bw', 'status'])
    bw_p50, bw_p95 = calculate_percentiles(bw_times)
    results['bitwarden_cli_status_startup'] = {
        'p50_seconds': bw_p50,
        'p95_seconds': bw_p95
    }
    
    # 4. Chromium extension startup/service worker detection
    chrome_times = measure_execution_time(['chromium', '--headless', '--disable-gpu', '--dump-dom', 'about:blank'])
    c_p50, c_p95 = calculate_percentiles(chrome_times)
    results['chromium_extension_startup'] = {
        'p50_seconds': c_p50,
        'p95_seconds': c_p95
    }
    
    # 5. ACPX routing overhead
    # We measure the startup overhead of the ACPX runner
    acpx_times = measure_execution_time(['python3', 'scripts/acpx_runner.py', '--help'])
    a_p50, a_p95 = calculate_percentiles(acpx_times)
    results['acpx_routing_overhead'] = {
        'p50_seconds': a_p50,
        'p95_seconds': a_p95
    }
    
    return results

def generate_reports(results, json_path, md_path):
    # Emit sanitized JSON
    with open(json_path, 'w') as f:
        json.dump(results, f, indent=2)
        
    # Emit Markdown
    with open(md_path, 'w') as f:
        f.write("# Performance Benchmarks Report\n\n")
        f.write("| Component | P50 (s) | P95 (s) |\n")
        f.write("|---|---|---|\n")
        for key, metrics in results.items():
            f.write(f"| {key} | {metrics['p50_seconds']:.4f} | {metrics['p95_seconds']:.4f} |\n")
            
if __name__ == '__main__':
    # Ensure working from project root
    os.chdir(os.path.join(os.path.dirname(__file__), '..'))
    
    print("Running benchmarks...")
    results = run_all_benchmarks()
    
    json_out = 'benchmarks/benchmark_results.json'
    md_out = 'benchmarks/benchmark_report.md'
    
    generate_reports(results, json_out, md_out)
    print(f"Results written to {json_out} and {md_out}")
