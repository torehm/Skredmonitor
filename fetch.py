#!/usr/bin/env python3
"""Samler skredsaker fra media (via RSS / Google News-søk) og varsler fra NVE/Varsom.
Skriver resultatet til docs/data.json. Kun standardbiblioteket trengs."""
import datetime as dt
import hashlib
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from pathlib import Path

ROOT = Path(__file__).parent
DATA = ROOT / "docs" / "data.json"
CFG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
UA = "Mozilla/5.0 (compatible; skredmonitor/1.0; personlig bruk)"
NVE = "https://api01.nve.no/hydrology/forecast/landslide/v1.0.10/api"

# Ord som må stå i tittelen for at saken regnes som en skredsak
KEYWORDS = re.compile(
    r"\b(\w*skred\w*|snøras|steinras|jordras|leirras|isras|fjellras|ras|raste|rasa|"
    r"rasfare|rasfarleg|rasområde\w*|utrasing\w*|steinsprang\w*|kvikkleire\w*|utglidning\w*)\b",
    re.IGNORECASE,
)
# Ord som gir treff i Google-søk
SEARCH_TERMS = "(skred OR ras OR jordskred OR steinskred OR snøskred OR leirskred OR steinsprang)"


def name_regex(names):
    # Store forbokstaver kreves, så «vik» (bukt) ikke forveksles med Vik
    return re.compile(r"\b(" + "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True)) + r")\b")


SF_RE = name_regex(CFG["sf_names"])
HO_RE = name_regex(CFG["ho_names"])


def http_get(url, tries=2):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
            with urllib.request.urlopen(req, timeout=25) as r:
                return r.read()
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(2)
    raise last


def google_news_url(query):
    q = f"{query} when:{CFG['google_when']}"
    return "https://news.google.com/rss/search?" + urllib.parse.urlencode(
        {"q": q, "hl": "no", "gl": "NO", "ceid": "NO:no"}
    )


def parse_feed(xml_bytes, fallback_source):
    """Returnerer liste med dicts: title, url, source, published (ISO, UTC)."""
    root = ET.fromstring(xml_bytes)
    out = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        if not title or not link:
            continue
        src_el = item.find("source")
        source = (src_el.text.strip() if src_el is not None and src_el.text else fallback_source)
        suffix = f" - {source}"
        if title.endswith(suffix):
            title = title[: -len(suffix)].strip()
        pub = item.findtext("pubDate")
        try:
            d = parsedate_to_datetime(pub).astimezone(dt.timezone.utc)
        except Exception:  # noqa: BLE001
            d = dt.datetime.now(dt.timezone.utc)
        out.append({"title": title, "url": link, "source": source, "published": d.isoformat(timespec="seconds")})
    return out


def detect_regions(text, default):
    regions = set()
    if SF_RE.search(text):
        regions.add("sf")
    if HO_RE.search(text):
        regions.add("ho")
    if not regions:
        regions.add(default if default in ("sf", "ho") else "annet")
    return regions


def norm_key(title):
    return re.sub(r"[^0-9a-zæøå]", "", title.lower())[:70]


def build_tasks():
    """(navn, url, standardregion, krev_stedsnavn, regional_kilde)

    krev_stedsnavn: saken må nevne et sted i regionen (eller «Vestland») i tittelen.
    regional_kilde: kilden dekker bare Vestland (f.eks. NRK Vestland), så alt derfra beholdes."""
    tasks = []
    for n in CFG["sf_queries"]:
        tasks.append((f"søk {n}", google_news_url(f'{SEARCH_TERMS} "{n}"'), "sf", False, False))
    for n in CFG["ho_queries"]:
        tasks.append((f"søk {n}", google_news_url(f'{SEARCH_TERMS} "{n}"'), "ho", False, False))
    for s in CFG["local_sources"]:
        tasks.append((s["name"], google_news_url(f"{SEARCH_TERMS} site:{s['domain']}"), s["region"], False, False))
    for s in CFG.get("regional_sources", []):
        tasks.append((s["name"], google_news_url(f"{SEARCH_TERMS} site:{s['domain']}"), "annet", False, True))
    for s in CFG["national_sources"]:
        tasks.append((s["name"], google_news_url(f"{SEARCH_TERMS} Vestland site:{s['domain']}"), "annet", True, False))
    for n in CFG["national_queries"]:
        tasks.append((f"søk {n}", google_news_url(f'{SEARCH_TERMS} "{n}"'), "annet", True, False))
    for f in CFG["direct_feeds"]:
        tasks.append((f["name"], f["url"], f["region"], False, True))
    return tasks


def is_blocked(source):
    s = source.lower()
    return any(b.lower() in s for b in CFG.get("blocked_sources", []))


def names_a_place(title):
    return bool(SF_RE.search(title) or HO_RE.search(title) or "Vestland" in title)


def collect_articles(existing):
    now = dt.datetime.now(dt.timezone.utc)
    cutoff = now - dt.timedelta(days=CFG["keep_days"])
    # Rydd bort gamle saker fra blokkerte kilder, og nasjonale saker uten stedstilknytning
    by_id = {
        a["id"]: a for a in existing
        if not is_blocked(a["source"]) and (a["regions"] != ["annet"] or a.get("trusted"))
    }
    ok = failed = 0
    for name, url, default, strict, trusted in build_tasks():
        try:
            items = parse_feed(http_get(url), name)
            ok += 1
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"  feil: {name}: {e}", file=sys.stderr)
            continue
        for it in items:
            if not KEYWORDS.search(it["title"]) or is_blocked(it["source"]):
                continue
            if strict and not names_a_place(it["title"]):
                continue
            if dt.datetime.fromisoformat(it["published"]) < cutoff:
                continue
            aid = hashlib.sha1(norm_key(it["title"]).encode()).hexdigest()[:12]
            regions = detect_regions(it["title"], default)
            if aid in by_id:
                a = by_id[aid]
                a["regions"] = sorted(set(a["regions"]) | regions)
                a["trusted"] = bool(a.get("trusted") or trusted)
                if it["source"] != a["source"] and all(o["source"] != it["source"] for o in a.get("also", [])):
                    a.setdefault("also", []).append({"source": it["source"], "url": it["url"]})
            else:
                by_id[aid] = {
                    "id": aid, "title": it["title"], "url": it["url"], "source": it["source"],
                    "published": it["published"], "first_seen": now.isoformat(timespec="seconds"),
                    "regions": sorted(regions), "also": [], "trusted": trusted,
                }
        time.sleep(0.4)
    print(f"Kilder: {ok} ok, {failed} feilet")
    if ok == 0:
        raise SystemExit("Ingen kilder svarte – avbryter uten å endre data.")
    kept = [a for a in by_id.values() if dt.datetime.fromisoformat(a["published"]) >= cutoff]
    kept.sort(key=lambda a: a["published"], reverse=True)
    return kept


def collect_ids(obj, found):
    """Finner (kommunenr, navn) uansett hvor de ligger i NVE-svaret."""
    if isinstance(obj, dict):
        i, n = obj.get("Id"), obj.get("Name")
        if i is not None and n and re.fullmatch(r"46\d\d", str(i)):
            found[str(i)] = n
        for v in obj.values():
            collect_ids(v, found)
    elif isinstance(obj, list):
        for v in obj:
            collect_ids(v, found)


def first(d, *keys):
    for k in keys:
        v = d.get(k)
        if v not in (None, "", []):
            return v
    return None


def fetch_nve():
    """Jordskredvarsler for Vestland (fylke 46), i dag og to dager frem. Nivå 2 og høyere."""
    today = dt.date.today()
    url = f"{NVE}/Warning/County/46/1/{today.isoformat()}/{(today + dt.timedelta(days=2)).isoformat()}"
    raw = json.loads(http_get(url))
    if not isinstance(raw, list):
        raise ValueError(f"Uventet svar fra NVE: {str(raw)[:200]}")
    out = []
    for w in raw:
        try:
            level = int(first(w, "ActivityLevel", "DangerLevel") or 0)
        except (TypeError, ValueError):
            level = 0
        if level < 2:
            continue
        muni = {}
        collect_ids(w, muni)
        regions = set()
        for mid in muni:
            regions.add("sf" if mid == "4602" or 4635 <= int(mid) <= 4651 else "ho")
        out.append({
            "id": str(first(w, "Id", "MasterId") or len(out)),
            "level": level,
            "text": first(w, "MainText", "WarningText", "EmergencyWarning", "LevelText") or "",
            "areas": sorted(muni.values()),
            "regions": sorted(regions) or ["sf", "ho"],
            "valid_from": first(w, "ValidFrom"),
            "valid_to": first(w, "ValidTo"),
        })
    if raw and not out:
        print("NVE: ingen varsler på nivå 2+ (nøkler i første varsel: " + ", ".join(sorted(raw[0].keys())) + ")")
    out.sort(key=lambda x: (-x["level"], x["valid_from"] or ""))
    return out


def site_url():
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if "/" in repo:
        owner, name = repo.split("/", 1)
        return f"https://{owner.lower()}.github.io/{name}/"
    return None


def build_notifications(new_articles, new_warnings):
    """Varsler om nye saker og NVE-varsler i Sogn og Fjordane. Maks fem enkeltvarsler per kjøring."""
    msgs = []
    for w in new_warnings:
        name = {2: "gult", 3: "oransje", 4: "rødt"}.get(w["level"], "")
        msgs.append({
            "title": f"Skredvarsel ({name} nivå)",
            "message": ", ".join(w["areas"]) or "Sogn og Fjordane",
            "click": "https://www.varsom.no/flom-og-jordskred/varsling/",
            "priority": 4 if w["level"] >= 3 else 3,
            "tags": ["warning"],
        })
    if len(new_articles) > 5:
        msgs.append({
            "title": f"{len(new_articles)} nye skredsaker",
            "message": "Nye saker fra Sogn og Fjordane",
            "click": site_url() or "https://www.varsom.no/flom-og-jordskred/",
            "tags": ["mountain"],
        })
    else:
        for a in new_articles:
            msgs.append({
                "title": f"Skred: {a['source']}",
                "message": a["title"],
                "click": a["url"],
                "tags": ["mountain"],
            })
    return msgs


def send_ntfy(msgs):
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if not topic or not msgs:
        return
    server = os.environ.get("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
    for m in msgs:
        body = json.dumps({"topic": topic, **m}, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            server, data=body, headers={"Content-Type": "application/json", "User-Agent": UA}
        )
        try:
            urllib.request.urlopen(req, timeout=20).read()
        except Exception as e:  # noqa: BLE001
            print(f"ntfy feilet: {e}", file=sys.stderr)
        time.sleep(0.5)


def find_new(prev, articles, warnings):
    prev_ids = {a["id"] for a in prev.get("articles", [])}
    recent = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=24)
    new_articles = [
        a for a in articles
        if a["id"] not in prev_ids and "sf" in a["regions"]
        and dt.datetime.fromisoformat(a["published"]) >= recent
    ]
    new_articles.sort(key=lambda a: a["published"])
    prev_w = {(w["id"], w["level"]) for w in prev.get("warnings", [])}
    new_warnings = [w for w in warnings if (w["id"], w["level"]) not in prev_w and "sf" in w["regions"]]
    return new_articles, new_warnings


def main():
    prev = {}
    if DATA.exists():
        prev = json.loads(DATA.read_text(encoding="utf-8"))
    articles = collect_articles(prev.get("articles", []))
    warnings, warn_err = [], None
    try:
        warnings = fetch_nve()
    except Exception as e:  # noqa: BLE001
        warn_err = str(e)[:200]
        warnings = prev.get("warnings", [])
        print(f"NVE feilet: {e}", file=sys.stderr)
    new = {"articles": articles, "warnings": warnings, "warnings_error": warn_err}
    old_cmp = {k: prev.get(k) for k in new}
    if old_cmp == new:
        print("Ingen endringer.")
        return
    new["updated"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    DATA.parent.mkdir(exist_ok=True)
    DATA.write_text(json.dumps(new, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Skrev {len(articles)} saker og {len(warnings)} varsler.")
    new_articles, new_warnings = find_new(prev, articles, warnings)
    msgs = build_notifications(new_articles, new_warnings)
    if msgs:
        print(f"Sender {len(msgs)} varsel(er) via ntfy" if os.environ.get("NTFY_TOPIC") else "NTFY_TOPIC er ikke satt – sender ikke varsler")
        send_ntfy(msgs)


if __name__ == "__main__":
    main()
