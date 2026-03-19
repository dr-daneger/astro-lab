#!/usr/bin/env python3
import argparse, math, re, sys, json
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime
import urllib.request
import pandas as pd
from bs4 import BeautifulSoup

# ---------- Configurable small-scope noise model ----------
# Default anchors for your 75 mm @ 60s (green channel), ~10 min bins:
# These came from your earlier PixInsight field calibration: they’re realistic for Bortle ~7.
# Points are (mag, predicted 10-min precision in ppt, including scintillation).
DEFAULT_NOISE_ANCHORS = [
    (10.8, 4.3),
    (11.2, 5.0),
    (11.8, 6.4),
    (12.3, 8.9),
    (12.7, 12.0),
]
DEFAULT_SCINT_PPT = 3.4  # included in anchors already; keep for reference

def interp_noise_10min_ppt(mag: float,
                           anchors: List[Tuple[float, float]] = DEFAULT_NOISE_ANCHORS
                           ) -> float:
    """Piecewise-linear interpolation of 10-min precision (ppt) vs mag."""
    pts = sorted(anchors, key=lambda x: x[0])
    if mag <= pts[0][0]: return pts[0][1]
    if mag >= pts[-1][0]: return pts[-1][1]
    for (x1,y1),(x2,y2) in zip(pts, pts[1:]):
        if x1 <= mag <= x2:
            t = (mag - x1) / max(1e-9, (x2 - x1))
            return y1 + t * (y2 - y1)
    return pts[-1][1]

# ---------- Swarthmore HTML planet-name parser (best-effort) ----------
def extract_planet_names_from_swarthmore_html(path: str) -> List[str]:
    """
    Heuristic parser: finds planet names in the saved Swarthmore HTML table.
    It looks for anchor/text patterns that resemble planet names (e.g., 'WASP-2 b', 'HD 189733 b').
    """
    try:
        with open(path, "rb") as f:
            html = f.read()
    except Exception as e:
        print(f"[warn] Could not read Swarthmore HTML: {e}", file=sys.stderr)
        return []

    soup = BeautifulSoup(html, "html.parser")

    # Grab all text in table rows and find patterns like ABC-123 b / HD 189733 b / HAT-P-xx b / TOI-xxxx b, etc.
    text = soup.get_text(" ", strip=True)
    # Common planet name patterns
    patterns = [
        r"\b[A-Z]{2,}-\d+\s?[A-Za-z]?\s?b\b",     # WASP-2 b, HAT-P-2 b, XO-7 b, etc.
        r"\bHD\s?\d+\s?b\b",                      # HD 189733 b
        r"\bHAT-P-\d+\s?b\b",
        r"\bKELT-\d+\s?b\b",
        r"\bTrES-\d+\s?b\b",
        r"\bQatar-\d+\s?b\b",
        r"\bKepler-\d+\s?b\b",
        r"\bTOI-\d+\s?(b|c|d|e)\b",
        r"\bCoRoT-\d+\s?b\b",
        r"\bNGTS-\d+\s?b\b",
        r"\bWASP-\d+\s?b\b",
        r"\bHATS-\d+\s?b\b",
        r"\bEPIC\s?\d+\s?b\b",
    ]
    name_set = set()
    for pat in patterns:
        for m in re.finditer(pat, text):
            name_set.add(m.group(0).strip())

    # Normalize (collapse extra spaces)
    cleaned = sorted({re.sub(r"\s+", " ", n) for n in name_set})
    return cleaned

# ---------- Core scoring ----------
def compute_score(depth_ppt: float, duration_h: float, mag: float,
                  bin_min: float = 10.0, beta: float = 1.3) -> Tuple[float, float]:
    """
    Returns (score, sigma10_ppt) for the given planet with your setup assumptions.
    score = (depth / (sigma10 * beta)) * sqrt(duration_min / bin_min)
    """
    sigma10 = interp_noise_10min_ppt(mag)
    n_bins = max(1.0, (duration_h * 60.0) / bin_min)
    score = (depth_ppt / max(1e-9, (sigma10 * beta))) * math.sqrt(n_bins)
    return score, sigma10

def choose_mag(row: Dict[str, Any]) -> Optional[float]:
    # Prefer Gaia G if present; else V; else R
    for k in ("gaia_g_mag", "v_mag", "r_mag"):
        v = row.get(k, None)
        try:
            if v is not None: return float(v)
        except Exception:
            pass
    return None

# ---------- Fetch + rank ----------
def fetch_exoclock_planets(json_cache: Optional[str] = None,
                           save_json: Optional[str] = "exoclock_planets.json") -> List[Dict[str, Any]]:
    import urllib.request, json, os, time
    url = "https://www.exoclock.space/database/planets_json"

    # 1) If a cache path is provided and exists, use it
    if json_cache and os.path.isfile(json_cache):
        with open(json_cache, "r", encoding="utf-8") as f:
            data = json.load(f)
        return _normalize_planets_json(data)

    # 2) Otherwise try network with retries/backoff
    headers = {"User-Agent": "exoclock-selector/1.0 (+https://example.com)"}
    req = urllib.request.Request(url, headers=headers)

    timeouts = [20, 35, 50, 65]  # seconds per attempt
    last_err = None
    for _, to in enumerate(timeouts, start=1):
        try:
            with urllib.request.urlopen(req, timeout=to) as r:
                raw = r.read().decode("utf-8", errors="replace")
                data = json.loads(raw)
            # Save cache if requested
            if save_json:
                try:
                    with open(save_json, "w", encoding="utf-8") as f:
                        json.dump(data, f)
                except Exception:
                    pass
            return _normalize_planets_json(data)
        except Exception as e:
            last_err = e
            # small backoff before next try
            time.sleep(1.0)

    raise RuntimeError(f"Failed to fetch ExoClock planets_json after {len(timeouts)} attempts: {last_err}")


def _normalize_planets_json(data: Any) -> List[Dict[str, Any]]:
    # Normalize to list[dict] no matter how it's structured
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for k in ("planets", "data", "results", "items"):
            v = data.get(k)
            if isinstance(v, list):
                return v
        if data and all(isinstance(v, dict) for v in data.values()):
            return list(data.values())
    raise ValueError(f"Unexpected JSON format from ExoClock: {type(data)}")

def build_dataframe(raw: List[Dict[str, Any]],
                    bin_min: float,
                    beta: float,
                    ):
    rows = []
    for row in raw:
        name = row.get("name", "").strip()
        mag = choose_mag(row)
        depth_mmag = row.get("depth_mmag")
        dur_h = row.get("duration_hours")
        if depth_mmag is None or dur_h is None or mag is None:
            continue
        try:
            depth_ppt = float(depth_mmag)  # mmag ~ ppt (good approx in this range)
            dur_h = float(dur_h)
            mag = float(mag)
        except Exception:
            continue

        score, sigma10 = compute_score(depth_ppt, dur_h, mag, bin_min=bin_min, beta=beta)
        rows.append({
            "name": name,
            "priority": row.get("priority"),
            "mag_pref": mag,
            "mag_type": ("G" if row.get("gaia_g_mag") is not None else
                         "V" if row.get("v_mag") is not None else
                         "R" if row.get("r_mag") is not None else "?"),
            "depth_ppt": depth_ppt,
            "duration_h": dur_h,
            "score": score,
            "pred_sigma10_ppt": sigma10,
            "min_telescope_inches": pd.to_numeric(row.get("min_telescope_inches"), errors="coerce"),
            "current_oc_min": pd.to_numeric(row.get("current_oc_min"), errors="coerce"),
            "t0_bjd_tdb": row.get("t0_bjd_tdb"),
            "t0_unc_d": pd.to_numeric(row.get("t0_unc"), errors="coerce"),
            "period_d": pd.to_numeric(row.get("period_days"), errors="coerce"),
            "period_unc_d": pd.to_numeric(row.get("period_unc"), errors="coerce"),
            "ra_j2000": row.get("ra_j2000"),
            "dec_j2000": row.get("dec_j2000"),
            "recent_observations": pd.to_numeric(row.get("recent_observations"), errors="coerce"),
            "total_observations": pd.to_numeric(row.get("total_observations"), errors="coerce"),
        })
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values("score", ascending=False).reset_index(drop=True)
    return df

# ---------- CLI ----------
def main():
    p = argparse.ArgumentParser(description="Rank exoplanet transits for a small scope using ExoClock JSON.")
    p.add_argument("--bin-min", type=float, default=10.0, help="Bin size in minutes (default 10).")
    p.add_argument("--beta", type=float, default=1.3, help="Red-noise factor ?? (default 1.3).")
    p.add_argument("--json-cache", type=str, default=None,
                   help="Path to a local planets_json file to use (skips network).")
    p.add_argument("--save-json", type=str, default="exoclock_planets.json",
                   help="Where to cache the downloaded JSON after a successful fetch.")
    p.add_argument("--mag-limit", type=float, default=12.5, help="Max preferred magnitude (G/V) (default 12.5).")
    p.add_argument("--depth-min", type=float, default=12.0, help="Minimum transit depth in ppt (default 12).")
    p.add_argument("--duration-min", type=float, default=1.5, help="Minimum duration in hours (default 1.5).")
    p.add_argument("--min-observations", type=int, default=0, help="Require at least this many total observations (default 0).")
    p.add_argument("--swarthmore-html", type=str, default=None, help="Path to saved Swarthmore HTML to intersect names (optional).")
    p.add_argument("--top", type=int, default=40, help="Print top N rows (default 40).")
    p.add_argument("--out-prefix", type=str, default=".", help="Folder to save CSVs (default current).")
    args = p.parse_args()

    # Fetch ExoClock
    raw = fetch_exoclock_planets(json_cache=args.json_cache, save_json=args.save_json)
    df_all = build_dataframe(raw, bin_min=args.bin_min, beta=args.beta)

    if df_all.empty:
        print("No data parsed from ExoClock. Exiting.")
        sys.exit(1)

    # Save a full ranked table
    ranked_path = f"{args.out_prefix.rstrip('/')}/targets_ranked.csv"
    df_all.to_csv(ranked_path, index=False)
    print(f"[saved] {ranked_path}  ({len(df_all)} rows)")

    # Apply your small-scope filters
    filt = (
        (df_all["mag_pref"] <= args.mag_limit) &
        (df_all["depth_ppt"] >= args.depth_min) &
        (df_all["duration_h"] >= args.duration_min) &
        (df_all["total_observations"].fillna(0) >= args.min_observations)
    )
    df_short = df_all[filt].copy().reset_index(drop=True)

    short_path = f"{args.out_prefix.rstrip('/')}/shortlist.csv"
    df_short.to_csv(short_path, index=False)
    print(f"[saved] {short_path}  ({len(df_short)} rows)")

    # Optional: intersect with Swarthmore page
    if args.swarthmore_html:
        sw_names = extract_planet_names_from_swarthmore_html(args.swarthmore_html)
        if sw_names:
            # Normalize both to simple lowercase, collapsed spaces/dashes
            def norm(s: str) -> str:
                s = re.sub(r"\s+", " ", s.strip().lower())
                s = s.replace("hatp", "hat-p")  # minor cleanup
                return s

            sw_norm = {norm(n) for n in sw_names}
            df_short["name_norm"] = df_short["name"].astype(str).apply(norm)
            inter = df_short[df_short["name_norm"].isin(sw_norm)].drop(columns=["name_norm"])
            out_path = f"{args.out_prefix.rstrip('/')}/shortlist_vs_swarthmore.csv"
            inter.to_csv(out_path, index=False)
            print(f"[saved] {out_path}  ({len(inter)} rows; intersection with Swarthmore page)")
        else:
            print("[warn] Could not extract names from Swarthmore HTML; skipped intersection.")

    # Console preview
    cols = ["name","priority","mag_pref","mag_type","depth_ppt","duration_h",
            "pred_sigma10_ppt","score","current_oc_min","t0_unc_d",
            "min_telescope_inches","recent_observations","total_observations"]
    print("\n=== Top candidates (after filters) ===")
    print(df_short[cols].head(args.top).to_string(index=False))

if __name__ == "__main__":
    main()




