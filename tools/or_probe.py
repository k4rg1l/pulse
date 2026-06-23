"""One-off OpenRouter API surface probe (research only — no changes to the app).

Hits a wide range of documented + guessed/undocumented endpoints with both the
user key and (if present) the management key, captures FULL JSON to
tools/_probe_out/, and prints a status summary. Read-only GETs only — NO paid
inference calls. Safe to run repeatedly.
"""
import json
import os
import sys
import pathlib
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config  # noqa: E402

OUT = pathlib.Path(__file__).parent / "_probe_out"
OUT.mkdir(exist_ok=True)

USER_KEY = config.API_KEY
# management key from settings.json
MGMT_KEY = ""
try:
    import json as _j
    appdata = os.environ.get("APPDATA") or os.path.expanduser("~")
    for d in ("Pulse", "OpenRouterPulse"):
        p = pathlib.Path(appdata) / d / "settings.json"
        if p.exists():
            MGMT_KEY = str(_j.loads(p.read_text(encoding="utf-8-sig")).get("management_api_key", "")).strip()
            if MGMT_KEY:
                break
except Exception as e:
    print("mgmt key load:", e)

MODEL = "anthropic/claude-opus-4.8"
BASE = "https://openrouter.ai"

# (label, method, url, which_key)  which_key: 'user' | 'mgmt' | 'none'
PROBES = [
    # --- documented public API (user key) ---
    ("key",                "GET", f"{BASE}/api/v1/key", "user"),
    ("credits",            "GET", f"{BASE}/api/v1/credits", "user"),
    ("models",             "GET", f"{BASE}/api/v1/models", "user"),
    ("models_category",    "GET", f"{BASE}/api/v1/models?category=programming", "user"),
    ("models_supported",   "GET", f"{BASE}/api/v1/models?supported_parameters=tools", "user"),
    ("model_endpoints",    "GET", f"{BASE}/api/v1/models/{MODEL}/endpoints", "user"),
    ("providers",          "GET", f"{BASE}/api/v1/providers", "user"),
    ("generation_bad",     "GET", f"{BASE}/api/v1/generation?id=nonexistent", "user"),
    # --- guessed / less-documented (user key) ---
    ("me",                 "GET", f"{BASE}/api/v1/me", "user"),
    ("auth_key",           "GET", f"{BASE}/api/v1/auth/key", "user"),
    ("models_user",        "GET", f"{BASE}/api/v1/models/user", "user"),
    ("parameters",         "GET", f"{BASE}/api/v1/parameters/{MODEL}", "user"),
    ("completions_meta",   "GET", f"{BASE}/api/v1/completions", "user"),
    ("activity_user",      "GET", f"{BASE}/api/v1/activity", "user"),
    ("keys_user",          "GET", f"{BASE}/api/v1/keys", "user"),
    # --- management key (org-scoped) ---
    ("activity_mgmt",      "GET", f"{BASE}/api/v1/activity", "mgmt"),
    ("keys_mgmt",          "GET", f"{BASE}/api/v1/keys", "mgmt"),
    ("key_mgmt",           "GET", f"{BASE}/api/v1/key", "mgmt"),
    ("credits_mgmt",       "GET", f"{BASE}/api/v1/credits", "mgmt"),
    # --- frontend / website API (often no auth) — the "dark" surface ---
    ("fe_models",          "GET", f"{BASE}/api/frontend/models", "none"),
    ("fe_models_find",     "GET", f"{BASE}/api/frontend/models/find", "none"),
    ("fe_all_providers",   "GET", f"{BASE}/api/frontend/all-providers", "none"),
    ("fe_model_endpoints", "GET", f"{BASE}/api/frontend/stats/endpoint?permaslug=anthropic/claude-opus-4.8&variant=standard", "none"),
    ("fe_rankings",        "GET", f"{BASE}/api/frontend/models/rankings", "none"),
    ("fe_trending",        "GET", f"{BASE}/api/frontend/trending", "none"),
    ("fe_uptime",          "GET", f"{BASE}/api/frontend/stats/uptime-hourly?permaslug=anthropic/claude-opus-4.8&variant=standard", "none"),
    ("fe_app_rankings",    "GET", f"{BASE}/api/frontend/models/apps?permaslug=anthropic/claude-opus-4.8&variant=standard", "none"),
    ("fe_providers_page",  "GET", f"{BASE}/api/frontend/providers", "none"),
]


def headers_for(which):
    if which == "user":
        return {"Authorization": f"Bearer {USER_KEY}"}
    if which == "mgmt":
        return {"Authorization": f"Bearer {MGMT_KEY}"} if MGMT_KEY else None
    return {}


def summarize(obj, depth=0):
    """Top-level key list (and first item keys for lists) — schema at a glance."""
    if isinstance(obj, dict):
        if "data" in obj and depth == 0:
            d = obj["data"]
            if isinstance(d, list) and d:
                return f"data=[{len(d)}] item keys: {sorted(d[0].keys()) if isinstance(d[0], dict) else type(d[0]).__name__}"
            if isinstance(d, dict):
                return f"data keys: {sorted(d.keys())}"
            return f"data={type(d).__name__}"
        return f"keys: {sorted(obj.keys())}"
    if isinstance(obj, list):
        return f"list[{len(obj)}] item keys: {sorted(obj[0].keys()) if obj and isinstance(obj[0], dict) else '?'}"
    return type(obj).__name__


print(f"user key: {bool(USER_KEY)} | mgmt key: {bool(MGMT_KEY)}\n")
print(f"{'LABEL':22} {'STATUS':6} SUMMARY")
print("-" * 100)
for label, method, url, which in PROBES:
    h = headers_for(which)
    if h is None:
        print(f"{label:22} {'SKIP':6} (no {which} key)")
        continue
    try:
        r = requests.request(method, url, headers=h, timeout=20)
        status = r.status_code
        try:
            body = r.json()
            (OUT / f"{label}.json").write_text(json.dumps(body, indent=2)[:200000], encoding="utf-8")
            summ = summarize(body)
        except Exception:
            txt = r.text[:2000]
            (OUT / f"{label}.txt").write_text(txt, encoding="utf-8")
            summ = f"non-json ({len(r.text)}b): {txt[:80]!r}"
        print(f"{label:22} {status:<6} {summ[:90]}")
    except Exception as e:
        print(f"{label:22} {'ERR':6} {type(e).__name__}: {str(e)[:70]}")
