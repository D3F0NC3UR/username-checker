#!/usr/bin/env python3
"""
Username checker — version clean & simple
========================================
• Demande le username si absent.
• Charge automatiquement le catalogue Sherlock (~1000+ sites) avec cache local.
• Utilise des miroirs/fallbacks + un preset intégré (≈50 sites) si le réseau bloque.
• Test asynchrone (aiohttp), export JSON/CSV.

Fonctionne bien sur Android (Pydroid) et desktop.
"""
from __future__ import annotations
import asyncio
import aiohttp
import argparse
import json
import csv
import os
import random
import re
import sys
import tempfile
from pathlib import Path
from typing import Dict, Any, Optional, Tuple
from datetime import datetime, timedelta

# ============================
# Réglages
# ============================
CATALOG_URL = "https://raw.githubusercontent.com/sherlock-project/sherlock/refs/heads/master/sherlock_project/resources/data.json"
CATALOG_FALLBACKS = [
    "https://github.com/sherlock-project/sherlock/raw/main/sherlock/resources/data.json",
    "https://cdn.jsdelivr.net/gh/sherlock-project/sherlock/sherlock/resources/data.json",
]
CACHE_PATH = Path(os.path.expanduser("~/.cache/username_checker/sherlock_data.json"))
CACHE_TTL_DAYS = 7
AUTO_TOP_DEFAULT = 1000  # nombre de sites à charger par défaut depuis le catalogue

DEFAULT_CONCURRENCY = 20
DEFAULT_TIMEOUT = 10
DEFAULT_RETRIES = 2

UA_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
]

USERNAME_ALLOWED = re.compile(r"^[a-zA-Z0-9_\-.]{1,30}$")

# Table globale des sites (chargée dynamiquement)
SITES: Dict[str, Dict[str, Any]] = {}

# ============================
# Utilitaires catalogue
# ============================

def cache_is_fresh(path: Path, ttl_days: int) -> bool:
    try:
        mtime = datetime.fromtimestamp(path.stat().st_mtime)
        return datetime.now() - mtime < timedelta(days=ttl_days)
    except Exception:
        return False


def download_to(path: Path, url: str, timeout: int = 30) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    import urllib.request
    req = urllib.request.Request(url, headers={
        "User-Agent": UA_POOL[0],
        "Accept": "application/json,text/plain,*/*",
    })
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp, open(tmp_path, "wb") as out:
            out.write(resp.read())
        tmp_path.replace(path)
    finally:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except Exception:
            pass


def ensure_catalog(url: str, cache_path: Path, ttl_days: int) -> Path:
    if not cache_path.exists() or not cache_is_fresh(cache_path, ttl_days):
        print("[i] Téléchargement du catalogue…")
        urls = [url] + CATALOG_FALLBACKS
        ok = False
        for u in urls:
            try:
                download_to(cache_path, u)
                ok = True
                print(f"[i] Catalogue téléchargé depuis: {u}")
                break
            except Exception as e:
                print(f"[!] Échec: {u} → {e}")
        if not ok:
            print("[!] Impossible de télécharger le catalogue — bascule sur le preset intégré.", file=sys.stderr)
    return cache_path


def load_sherlock_sites(path: str, top: Optional[int] = None) -> Dict[str, Dict[str, Any]]:
    """Convertit le data.json Sherlock en table SITES.
    • errorType == 'status_code' → 404 = dispo ; 200/3xx = pris
    • errorType == 'message' + errorMsg → regex dans le body *indiquant l'absence*
    Trie par 'rank' si présent. Applique top si fourni.
    """
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    items = list(raw.items())

    def order_key(it):
        name, cfg = it
        try:
            return int(cfg.get("rank", 1_000_000))
        except Exception:
            return 1_000_000

    items.sort(key=order_key)
    if top is not None:
        items = items[:top]

    out: Dict[str, Dict[str, Any]] = {}
    for name, cfg in items:
        et = cfg.get("errorType") or cfg.get("error_type")
        url = cfg.get("url") or cfg.get("uri_check")
        if not url:
            continue
        key = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:40] or name.lower()
        entry: Dict[str, Any] = {"url": url.replace("{}", "{username}")}
        if et == "status_code":
            entry["exists_if"] = {"status_in": [200, 301, 302]}
            entry["not_found_if"] = {"status_in": [404]}
        elif et == "message" and cfg.get("errorMsg"):
            entry["exists_if"] = {"status_in": [200, 301, 302]}
            entry["not_found_if"] = {"body_regex": cfg["errorMsg"]}
        else:
            continue
        out[key] = entry
    return out

# ============================
# Preset intégré (fallback) — concise & sans pièges de guillemets
# ============================
FALLBACK_PAIRS = [
    # Réseaux / vidéo / créateurs
    ("instagram", "https://www.instagram.com/{username}/"),
    ("x", "https://x.com/{username}"),
    ("twitter", "https://twitter.com/{username}"),
    ("youtube", "https://www.youtube.com/@{username}"),
    ("tiktok", "https://www.tiktok.com/@{username}"),
    ("twitch", "https://www.twitch.tv/{username}"),
    ("snapchat", "https://www.snapchat.com/add/{username}"),
    ("pinterest", "https://www.pinterest.com/{username}/"),
    ("reddit", "https://www.reddit.com/user/{username}"),
    ("discord", "https://discord.com/users/{username}"),
    # Dev / packaging / data
    ("github", "https://github.com/{username}"),
    ("gitlab", "https://gitlab.com/{username}"),
    ("bitbucket", "https://bitbucket.org/{username}"),
    ("npm", "https://www.npmjs.com/~{username}"),
    ("pypi", "https://pypi.org/user/{username}/"),
    ("dockerhub", "https://hub.docker.com/u/{username}"),
    ("crates", "https://crates.io/users/{username}"),
    ("rubygems", "https://rubygems.org/profiles/{username}"),
    ("kaggle", "https://www.kaggle.com/{username}"),
    ("huggingface", "https://huggingface.co/{username}"),
    # Blogging / écriture
    ("medium", "https://medium.com/@{username}"),
    ("devto", "https://dev.to/{username}"),
    ("substack", "https://{username}.substack.com"),
    # Design / photo
    ("behance", "https://www.behance.net/{username}"),
    ("dribbble", "https://dribbble.com/{username}"),
    ("deviantart", "https://www.deviantart.com/{username}"),
    ("flickr", "https://www.flickr.com/people/{username}"),
    # Jeux / communautés
    ("steam", "https://steamcommunity.com/id/{username}"),
    ("itch", "https://{username}.itch.io"),
    ("chesscom", "https://www.chess.com/member/{username}"),
    ("lichess", "https://lichess.org/@/{username}"),
    # Divers
    ("aboutme", "https://about.me/{username}"),
]

def build_sites_from_pairs(pairs):
    out = {}
    for key, url in pairs:
        out[key] = {
            "url": url,
            "exists_if": {"status_in": [200, 301, 302]},
            "not_found_if": {"status_in": [404]},
        }
    return out

FALLBACK_SITES = build_sites_from_pairs(FALLBACK_PAIRS)

# ============================
# HTTP / logique
# ============================

def decide_with_rules(status: int, body: Optional[str], rules: Dict[str, Any]) -> Optional[bool]:
    if not rules:
        return None
    if "status_in" in rules and status in rules["status_in"]:
        return True
    if body is not None and "body_regex" in rules:
        pat = rules["body_regex"]
        if re.search(pat, body, flags=re.IGNORECASE | re.DOTALL):
            return True
    return None


async def fetch(session: aiohttp.ClientSession, url: str, timeout: int) -> Tuple[int, Optional[str]]:
    # petit jitter anti-burst
    await asyncio.sleep(random.uniform(0.05, 0.25))
    try:
        async with session.get(url, allow_redirects=True, timeout=timeout) as resp:
            status = resp.status
            text = None
            if status == 200:
                text = await resp.text(errors="ignore")
                if len(text) > 200_000:
                    text = text[:200_000]
            return status, text
    except asyncio.TimeoutError:
        return 0, None
    except aiohttp.ClientError:
        return 0, None


async def check_one(session: aiohttp.ClientSession, site: str, username: str, cfg: Dict[str, Any], timeout: int, retries: int) -> Dict[str, Any]:
    url = cfg["url"].format(username=username)
    attempt = 0
    last_status, last_body = 0, None
    while attempt <= retries:
        status, body = await fetch(session, url, timeout)
        last_status, last_body = status, body
        if status != 0:
            break
        await asyncio.sleep(0.4 * (attempt + 1))
        attempt += 1

    exists = decide_with_rules(last_status, last_body, cfg.get("exists_if", {}))
    not_found = decide_with_rules(last_status, last_body, cfg.get("not_found_if", {}))

    if exists is True:
        available = False
    elif not_found is True:
        available = True
    else:
        available = None

    return {"site": site, "url": url, "status": last_status, "available": available}


async def run(username: str, sites: list[str], concurrency: int, timeout: int, retries: int) -> Dict[str, Any]:
    headers = {
        "User-Agent": random.choice(UA_POOL),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,fr-FR;q=0.8",
        "Connection": "keep-alive",
    }
    connector = aiohttp.TCPConnector(limit_per_host=concurrency)
    sem = asyncio.Semaphore(concurrency)

    async with aiohttp.ClientSession(headers=headers, connector=connector) as session:
        async def task(name: str):
            async with sem:
                return await check_one(session, name, username, SITES[name], timeout, retries)
        results = await asyncio.gather(*(task(s) for s in sites))

    return {"username": username, "results": results}


# ============================
# Affichage / export
# ============================

def pretty_print(data: Dict[str, Any]) -> None:
    from shutil import get_terminal_size
    width = max(60, min(get_terminal_size((100, 20)).columns, 120))
    print("\n" + "=" * width)
    print(f"Résultats pour: {data['username']}")
    print("-" * width)
    for r in data["results"]:
        if r["available"] is True:
            state = "DISPONIBLE"
        elif r["available"] is False:
            state = "PRIS"
        else:
            state = "INCONNU"
        print(f"{r['site']:<20} | {state:<10} | HTTP {r['status']:<3} | {r['url']}")
    print("=" * width + "\n")


def export_json(path: str, data: Dict[str, Any]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def export_csv(path: str, data: Dict[str, Any]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", newline="", encoding="utf-8") as f:
        wr = csv.writer(f)
        wr.writerow(["site", "available", "status", "url"])
        for r in data["results"]:
            wr.writerow([r["site"], r["available"], r["status"], r["url"]])


# ============================
# CLI
# ============================

def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Vérifie la disponibilité d'un username sur des centaines de sites")
    p.add_argument("username", nargs="?", help="Nom d'utilisateur à tester (sinon demandé)")
    p.add_argument("--sites", nargs="*", default=None, help="Sous-ensemble de sites (défaut: tous chargés)")
    p.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)
    p.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    p.add_argument("--retries", type=int, default=DEFAULT_RETRIES)
    p.add_argument("--json", dest="json_path")
    p.add_argument("--csv", dest="csv_path")
    p.add_argument("--no-auto", action="store_true", help="Ne pas charger automatiquement le catalogue Sherlock")
    p.add_argument("--catalog-url", default=CATALOG_URL)
    p.add_argument("--cache", default=str(CACHE_PATH))
    p.add_argument("--auto-top", type=int, default=AUTO_TOP_DEFAULT, help="Limiter le nombre de sites auto-chargés")
    p.add_argument("--ttl", type=int, default=CACHE_TTL_DAYS, help="Fraîcheur max du cache (jours)")
    p.add_argument("--catalog-file", help="Chemin local vers un data.json (si réseau bloqué)")
    return p.parse_args(argv)


# ============================
# Main
# ============================

def main(argv: list[str]) -> int:
    args = parse_args(argv)

    # 1) Username
    username = args.username or input("Entrez un username: ").strip()
    if not USERNAME_ALLOWED.match(username):
        print("[!] Username invalide (lettres/chiffres/._-), max 30", file=sys.stderr)
        return 2

    # 2) Chargement des sites
    if not args.no_auto:
        # a) fichier local si fourni (utile sur Android quand le navigateur télécharge mieux)
        if args.catalog_file and Path(args.catalog_file).exists():
            try:
                loaded = load_sherlock_sites(args.catalog_file, top=args.auto_top)
                if loaded:
                    SITES.clear(); SITES.update(loaded)
                    print(f"[i] Catalogue local chargé: {args.catalog_file}")
            except Exception as e:
                print(f"[!] Erreur lecture --catalog-file: {e}", file=sys.stderr)
        # b) sinon, téléchargement + cache
        if not SITES:
            cache_path = Path(args.cache)
            ensure_catalog(args.catalog_url, cache_path, args.ttl)
            if cache_path.exists():
                try:
                    loaded = load_sherlock_sites(str(cache_path), top=args.auto_top)
                    if loaded:
                        SITES.clear(); SITES.update(loaded)
                except Exception as e:
                    print(f"[!] Erreur de lecture du catalogue: {e}", file=sys.stderr)

    # c) si toujours rien, preset fallback intégré
    if not SITES:
        SITES.update(FALLBACK_SITES)
        print(f"[i] Mode secours: {len(SITES)} sites intégrés.")

    # 3) Liste finale de sites
    if args.sites is None or len(args.sites) == 0:
        sites = list(SITES.keys())
    else:
        unknown = [s for s in args.sites if s not in SITES]
        if unknown:
            print(f"[!] Sites inconnus: {', '.join(unknown)}", file=sys.stderr)
            # affiche un aperçu de ceux disponibles
            sample = ", ".join(list(SITES.keys())[:20])
            print(f"    Exemples disponibles: {sample}…")
            return 2
        sites = args.sites

    print(f"[i] Sites chargés: {len(SITES)} | Tests: {len(sites)}")

    # 4) Exécution
    data = asyncio.run(run(username, sites, args.concurrency, args.timeout, args.retries))
    pretty_print(data)

    # 5) Exports
    if args.json_path:
        export_json(args.json_path, data)
        print(f"JSON -> {args.json_path}")
    if args.csv_path:
        export_csv(args.csv_path, data)
        print(f"CSV  -> {args.csv_path}")

    any_taken = any(r["available"] is False for r in data["results"])
    return 1 if any_taken else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
