"""Probe the REAL OpenRouter frontend API (/api/frontend/v1/*), discovered by
grepping the Next.js bundles. Read-only GETs only. Captures JSON to _probe_out/fe2_*.json
and prints a compact schema summary for each.
"""
import json, os, sys, pathlib, requests
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config  # noqa

OUT = pathlib.Path(__file__).parent / "_probe_out"
OUT.mkdir(exist_ok=True)
BASE = "https://openrouter.ai"
PERMA = "anthropic/claude-opus-4.8"
VARIANT = "standard"

USER_KEY = config.API_KEY
MGMT_KEY = ""
try:
    appdata = os.environ.get("APPDATA") or os.path.expanduser("~")
    for d in ("Pulse", "OpenRouterPulse"):
        p = pathlib.Path(appdata) / d / "settings.json"
        if p.exists():
            MGMT_KEY = str(json.loads(p.read_text(encoding="utf-8-sig")).get("management_api_key", "")).strip()
            if MGMT_KEY:
                break
except Exception as e:
    print("mgmt key load:", e)

# (label, path, params, auth)  auth: none|user|mgmt
P = [
    ("catalog_models",        "/api/frontend/v1/catalog/models", {}, "none"),
    ("models_find",           "/api/frontend/v1/models/find", {}, "none"),
    ("author_models",         "/api/frontend/v1/author-models", {"authorSlug": "anthropic"}, "none"),
    ("provider_filters",      "/api/frontend/v1/provider-filters", {}, "none"),
    ("all_providers",         "/api/frontend/all-providers", {}, "none"),
    # stats
    ("stats_endpoint",        "/api/frontend/v1/stats/endpoint", {"permaslug": PERMA, "variant": VARIANT}, "none"),
    ("stats_uptime_hourly",   "/api/frontend/v1/stats/uptime-hourly", {"permaslug": PERMA, "variant": VARIANT}, "none"),
    ("stats_uptime_recent",   "/api/frontend/v1/stats/uptime-recent", {"permaslug": PERMA, "variant": VARIANT}, "none"),
    ("stats_router_activity", "/api/frontend/v1/stats/router-activity", {"permaslug": PERMA, "variant": VARIANT}, "none"),
    ("stats_router_activity2","/api/frontend/v1/stats/router-activity", {}, "none"),
    # rankings
    ("rk_models",             "/api/frontend/v1/rankings/models", {}, "none"),
    ("rk_apps",               "/api/frontend/v1/rankings/apps", {"permaslug": PERMA, "variant": VARIANT}, "none"),
    ("rk_apps_global",        "/api/frontend/v1/rankings/apps", {}, "none"),
    ("rk_chart",              "/api/frontend/v1/rankings/model-rankings-chart", {}, "none"),
    ("rk_market_share",       "/api/frontend/v1/rankings/market-share", {}, "none"),
    ("rk_task_spend",         "/api/frontend/v1/rankings/task-spend", {}, "none"),
    ("rk_performance",        "/api/frontend/v1/rankings/performance", {}, "none"),
    ("rk_context_length",     "/api/frontend/v1/rankings/context-length", {}, "none"),
    ("rk_benchmarks",         "/api/frontend/v1/rankings/benchmarks", {}, "none"),
    ("rk_tools",              "/api/frontend/v1/rankings/tools", {}, "none"),
    ("rk_natural_language",   "/api/frontend/v1/rankings/natural-language", {}, "none"),
    ("rk_programming_lang",   "/api/frontend/v1/rankings/programming-language", {}, "none"),
    ("rk_use_case_category",  "/api/frontend/v1/rankings/use-case-category", {}, "none"),
    ("rk_images",             "/api/frontend/v1/rankings/images", {}, "none"),
    ("rk_image_output",       "/api/frontend/v1/rankings/image-output", {}, "none"),
    # private (try user + mgmt)
    ("priv_models_user",      "/api/frontend/v1/private/models", {}, "user"),
    ("priv_provprefs_user",   "/api/frontend/v1/private/provider-preferences", {}, "user"),
    ("priv_uptime_recent",    "/api/frontend/v1/private/stats/uptime-recent-private", {"permaslug": PERMA}, "user"),
    # documented dataset
    ("ds_rankings_daily",     "/api/v1/datasets/rankings-daily", {}, "user"),
]


def hdr(auth):
    if auth == "user":
        return {"Authorization": f"Bearer {USER_KEY}"}
    if auth == "mgmt":
        return {"Authorization": f"Bearer {MGMT_KEY}"} if MGMT_KEY else None
    return {}


def shape(o, d=0):
    if isinstance(o, dict):
        if "data" in o and d == 0:
            return "data->" + shape(o["data"], 1) + f"  (+meta={list(o.get('meta',{}).keys()) if 'meta' in o else '-'})"
        ks = sorted(o.keys())
        return "{" + ",".join(ks[:25]) + ("...}" if len(ks) > 25 else "}")
    if isinstance(o, list):
        return f"[{len(o)}] " + (shape(o[0], d + 1) if o else "")
    return type(o).__name__


print(f"user={bool(USER_KEY)} mgmt={bool(MGMT_KEY)}\n")
print(f"{'LABEL':24} {'CODE':5} SHAPE")
print("-" * 110)
sess = requests.Session()
sess.headers["User-Agent"] = "Mozilla/5.0"
for label, path, params, auth in P:
    h = hdr(auth)
    if h is None:
        print(f"{label:24} SKIP  (no {auth} key)")
        continue
    try:
        r = sess.get(BASE + path, params=params, headers=h, timeout=25)
        ct = r.headers.get("content-type", "")
        if "json" in ct or r.text[:1] in "{[":
            try:
                body = r.json()
                (OUT / f"fe2_{label}.json").write_text(json.dumps(body, indent=2)[:400000], encoding="utf-8")
                print(f"{label:24} {r.status_code:<5} {shape(body)[:88]}")
            except Exception:
                print(f"{label:24} {r.status_code:<5} JSON-PARSE-FAIL {r.text[:60]!r}")
        else:
            print(f"{label:24} {r.status_code:<5} HTML/{len(r.text)}b (not an API route)")
    except Exception as e:
        print(f"{label:24} ERR   {type(e).__name__}: {str(e)[:60]}")
