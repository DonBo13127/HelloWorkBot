# main.py
"""
Scraper éthique (HelloWork example) + intégration Notion.

Usage:
    python main.py --query "developpeur python" --pages 2
"""
import os
import sys
import time
import argparse
import logging
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

# charge .env si présent (local). Sur Replit/GitHub, utilise les Secrets.
load_dotenv()

from utils import (
    check_robots_allowed,
    rand_sleep,
    append_json,
    save_json,
    DEFAULT_USER_AGENT,
)
from notion_client import upsert_offer_to_notion  # assume file present

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)

HEADERS = {
    "User-Agent": os.environ.get("DEFAULT_USER_AGENT", DEFAULT_USER_AGENT),
    "Accept-Language": "fr-FR,fr;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
}

# Default base (HelloWork example) — adapte si tu changes de site
BASE_DOMAIN = "https://www.hellowork.com"
SEARCH_PATH = "/fr-fr/recherche-emploi/"

def fetch_page(session: requests.Session, url: str, params: dict = None, timeout: int = 20) -> str:
    """Récupère le HTML d'une page en gérant erreurs."""
    try:
        resp = session.get(url, params=params, timeout=timeout)
        resp.raise_for_status()
        return resp.text
    except requests.RequestException as e:
        logging.error("Erreur HTTP pour %s : %s", url, e)
        raise

def parse_hellowork_list(html: str, base_url: str = BASE_DOMAIN) -> list:
    """
    Parse le HTML de la page de résultats HelloWork et renvoie une liste d'offres.
    Adapte les sélecteurs CSS si nécessaire.
    """
    soup = BeautifulSoup(html, "lxml")
    results = []

    # Sélection générique - à adapter si le site change
    # On cherche des conteneurs d'offres (article, div.job-item, .result-item...)
    offers = soup.select("article, .job-item, .result-item, .results__item")
    if not offers:
        # fallback: essayer un autre container commun
        offers = soup.select(".listing-item, .annonce, .offer")
    for off in offers:
        # titre
        title_tag = off.select_one("h2 a, a.job-title, a.offer-title, .job-title a")
        # société
        company_tag = off.select_one(".company, .job-company, .offer-company, .company-name")
        # lieu
        location_tag = off.select_one(".locality, .job-location, .offer-location")
        # date
        date_tag = off.select_one(".date, .job-date, .offer-date")
        link = None
        if title_tag and title_tag.has_attr("href"):
            link = urljoin(base_url, title_tag["href"])
        title = title_tag.get_text(strip=True) if title_tag else None
        company = company_tag.get_text(strip=True) if company_tag else None
        location = location_tag.get_text(strip=True) if location_tag else None
        date = date_tag.get_text(strip=True) if date_tag else None

        if title:
            results.append({
                "title": title,
                "company": company,
                "location": location,
                "date": date,
                "link": link,
                "source": "hellowork"
            })
    return results

def run_search(query: str, pages: int = 1, base_domain: str = BASE_DOMAIN, search_path: str = SEARCH_PATH) -> list:
    """
    Lance la recherche paginée et retourne la liste complète d'offres.
    """
    session = requests.Session()
    session.headers.update(HEADERS)

    # Vérifie robots.txt pour la racine du domaine et le chemin de recherche
    root_url = base_domain
    if not check_robots_allowed(root_url, user_agent=HEADERS["User-Agent"], path=search_path):
        logging.error("Accès refusé par robots.txt pour %s%s — arrêt.", base_domain, search_path)
        return []

    all_offers = []
    for p in range(1, pages + 1):
        # construit l'URL / paramètres — adapter si le site attend d'autres paramètres
        url = urljoin(base_domain, search_path)
        params = {"q": query, "page": p}

        logging.info("Récupération page %d pour '%s' — %s %s", p, query, url, params)
        try:
            html = fetch_page(session, url, params=params)
        except Exception:
            logging.warning("Échec récupération page %d — saut.", p)
            continue

        offers = parse_hellowork_list(html, base_url=base_domain)
        logging.info("Offres trouvées sur la page %d : %d", p, len(offers))

        for o in offers:
            # tentative d'envoi à Notion (upsert) — si erreur -> sauvegarde locale
            try:
                res = upsert_offer_to_notion(o)
                status = res.get("status")
                if status == "created":
                    logging.info("Ajouté à Notion : %s", o.get("title"))
                else:
                    logging.debug("Existe déjà dans Notion : %s", o.get("title"))
            except Exception as e:
                logging.warning("Erreur Notion pour '%s' : %s — sauvegarde locale.", o.get("title"), e)
                append_json(o)
            all_offers.append(o)

        # pause aléatoire avant la page suivante pour rester poli
        if p < pages:
            rand_sleep(2.0, 5.0)

    # sauvegarde finale locale (complète) en plus de Notion/fallback
    try:
        save_json(all_offers, filename="data/results.json")
    except Exception:
        logging.debug("Impossible de sauvegarder data/results.json (permission ?).")

    return all_offers

def main():
    parser = argparse.ArgumentParser(description="Scraper éthique HelloWork -> Notion")
    parser.add_argument("--query", "-q", required=True, help="Terme de recherche, ex: 'developpeur python'")
    parser.add_argument("--pages", "-p", type=int, default=1, help="Nombre de pages à parcourir")
    parser.add_argument("--base", "-b", default=BASE_DOMAIN, help="Domaine de base (par défaut HelloWork)")
    parser.add_argument("--path", default=SEARCH_PATH, help="Chemin de recherche (défaut HelloWork)")
    args = parser.parse_args()

    logging.info("Lancement du scraper pour '%s' (%d pages)", args.query, args.pages)
    results = run_search(args.query, pages=args.pages, base_domain=args.base, search_path=args.path)
    logging.info("Terminé. Offres totales récupérées: %d", len(results))

if __name__ == "__main__":
    main()
