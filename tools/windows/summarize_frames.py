import argparse
import collections
import json
import math
import re
from pathlib import Path

parser = argparse.ArgumentParser(description='Summarize flip completion intervals, not confirmed display FPS.')
parser.add_argument('log', type=Path)
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args()
groups = collections.defaultdict(list)
with args.log.open(encoding='utf-8', errors='replace') as stream:
    for line in stream:
        if not line.startswith('[FrameTiming] '):
            continue
        fields = dict(re.findall(r'(\w+)=([^\s]+)', line))
        if fields.get('endpoint') != 'flip_complete' or 'flip_interval_ms' not in fields:
            continue
        try:
            value = float(fields['flip_interval_ms'])
        except ValueError:
            continue
        if math.isfinite(value) and value > 0:
            groups[fields.get('output', 'unknown')].append(value)

def percentile(values, percent):
    position = (len(values) - 1) * percent / 100
    lower = int(position)
    upper = min(lower + 1, len(values) - 1)
    return values[lower] + (values[upper] - values[lower]) * (position - lower)

report = {'endpoint': 'flip_complete', 'display_confirmed': False, 'outputs': {}}
for output, samples in groups.items():
    values = sorted(samples)
    report['outputs'][output] = {'intervals': len(values), 'mean_ms': sum(values) / len(values),
        'p50_ms': percentile(values, 50), 'p95_ms': percentile(values, 95), 'p99_ms': percentile(values, 99),
        'flip_completion_rate_per_second': 1000 * len(values) / sum(values)}
if not groups:
    report['status'] = 'no usable frame intervals; no FPS estimate available'
args.output.write_text(json.dumps(report, indent=2), encoding='utf-8')
print(json.dumps(report, indent=2))
