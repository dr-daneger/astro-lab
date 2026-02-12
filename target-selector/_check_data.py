import json
from pathlib import Path
path = Path('exoclock_planets.json')
data = json.loads(path.read_text(encoding='utf-8'))
if isinstance(data, dict):
    rows = list(data.values())
else:
    rows = data
keepers = []
skipped = []
for row in rows:
    name = row.get('name', '').strip()
    mag = row.get('gaia_g_mag') or row.get('v_mag') or row.get('r_mag')
    depth = row.get('depth_mmag')
    dur = row.get('duration_hours')
    if mag is None or depth is None or dur is None:
        skipped.append((name, mag, depth, dur))
    else:
        keepers.append(name)
print(f"total rows: {len(rows)}")
print(f"usable rows: {len(keepers)}")
print(f"skipped rows: {len(skipped)}")
print('sample skipped:', skipped[:5])
