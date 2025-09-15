# main.py
"""
Job Applier HelloWork (scraping + GPT + Notion)
- Scraping HelloWork pour récupérer les offres
- Lettres de motivation générées uniquement par GPT
- Upsert automatique dans Notion avec CV FR/ES
"""

import os
import sys
import time
import logging
from urllib.parse import urljoin
from typing import List, Dict, Optional

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

# Charger les secrets
load_dotenv()

# Logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", handlers=[logging.StreamHandler(sys.stdout)])
logger = logging.getLogger("job-applier")

# Config
NOTION_TOKEN = os.environ.get("NOTION_TOKEN")
NOTION_DATABASE_ID = os.environ.get("NOTION_DATABASE_ID")
CV_FR_URL = os.environ.get("CV_FR_URL")
CV_ES_URL = os.environ.get("CV_ES_URL")
USER_AGENT = os.environ.get("DEFAULT_USER_AGENT", "Mozilla/5.0 (compatible; YacineBot/1.0)")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")

if not OPENAI_API_KEY:
    logger.error("OPENAI_API_KEY manquante, le script ne peut pas générer les lettres GPT.")
    sys.exit(1)

import openai
openai.api_key = OPENAI_API_KEY

# Notion client
from notion_client import Client
notion = Client(auth=NOTION_TOKEN)

# Session requests
session = requests.Session()
session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "fr-FR,fr;q=0.9"})

# HelloWork
BASE_DOMAIN = "https://www.hellowork.com"
SEARCH_PATH = "/fr-fr/recherche-emploi/"

# Polite sleep
def rand_sleep(a=1.0, b=3.0):
    import random
    time.sleep(random.uniform(a,b))

# --- Scraping ---
def fetch_search_page(query: str, page: int = 1) -> Optional[str]:
    url = urljoin(BASE_DOMAIN, SEARCH_PATH)
    params = {"q": query, "page": page}
    try:
        r = session.get(url, params=params, timeout=20)
        r.raise_for_status()
        return r.text
    except Exception as e:
        logger.error("Erreur fetch_search_page: %s", e)
        return None

def parse_search_results(html: str) -> List[Dict]:
    soup = BeautifulSoup(html, "lxml")
    items = []
    offers = soup.select("article, .job-item, .result-item, .results__item")
    if not offers:
        offers = soup.select(".listing-item, .annonce, .offer, .job-card")
    for off in offers:
        title_tag = off.select_one("h2 a, a.job-title, a.offer-title, .job-title a")
        company_tag = off.select_one(".company, .job-company, .offer-company, .company-name")
        location_tag = off.select_one(".locality, .job-location, .offer-location")
        date_tag = off.select_one(".date, .job-date, .offer-date")
        link = urljoin(BASE_DOMAIN, title_tag["href"]) if title_tag and title_tag.has_attr("href") else None
        title = title_tag.get_text(strip=True) if title_tag else None
        company = company_tag.get_text(strip=True) if company_tag else None
        location = location_tag.get_text(strip=True) if location_tag else None
        date = date_tag.get_text(strip=True) if date_tag else None
        if title:
            items.append({
                "title": title,
                "company": company,
                "location": location,
                "date": date,
                "link": link,
                "source": "hellowork"
            })
    return items

# --- GPT Lettres ---
def generate_cover_letter_gpt(offer: Dict, lang: str = "fr") -> str:
    prompt = (
        f"Rédige une lettre de motivation professionnelle en {'français' if lang=='fr' else 'espagnol'} "
        f"pour postuler au poste '{offer.get('title')}' chez '{offer.get('company')}'. "
        f"Courte, impactante, 6 à 8 phrases, ton chaleureux et convaincant, adaptée pour candidature en ligne."
    )
    try:
        resp = openai.ChatCompletion.create(
            model="gpt-4o-mini",
            messages=[{"role":"user","content":prompt}],
            max_tokens=300,
            temperature=0.7
        )
        return resp['choices'][0]['message']['content'].strip()
    except Exception as e:
        logger.error("Erreur génération lettre GPT: %s", e)
        return f"Bonjour,\n\nJe souhaite postuler pour le poste {offer.get('title')} chez {offer.get('company')}.\nCordialement,\nYacine Bedhouche"

# --- Notion ---
def build_properties_from_offer(offer: Dict) -> Dict:
    properties = {
        "Title": {"title": [{"text": {"content": offer.get("title", "Sans titre")}}]},
        "Company": {"rich_text": [{"text": {"content": offer.get("company", "")}}]},
        "Link": {"url": offer.get("link", "")},
        "Status": {"select": {"name": offer.get("status", "Saved")}},
        "CoverLetter": {"rich_text": [{"text": {"content": offer.get("cover_letter", "")}}]},
        "CV": {"url": offer.get("cv_url", "")}
    }
    return properties

def upsert_offer_to_notion(offer: Dict) -> Dict:
    try:
        existing_pages = notion.databases.query(
            **{
                "database_id": NOTION_DATABASE_ID,
                "filter": {"property":"Link","url":{"equals":offer.get("link")}}
            }
        )
        props = build_properties_from_offer(offer)
        if existing_pages.get("results"):
            page_id = existing_pages["results"][0]["id"]
            notion.pages.update(page_id=page_id, properties=props)
            logger.info("Offre mise à jour dans Notion: %s", offer.get("title"))
            return {"status":"updated", "page_id":page_id}
        else:
            page = notion.pages.create(parent={"database_id":NOTION_DATABASE_ID}, properties=props)
            logger.info("Nouvelle offre ajoutée dans Notion: %s", offer.get("title"))
            return {"status":"created", "page_id":page["id"]}
    except Exception as e:
        logger.error("Erreur Notion: %s", e)
        return {"status":"error","error":str(e)}

# --- Main ---
def run(query: str, pages: int = 1, lang_pref: str = "fr"):
    all_offers = []
    for p in range(1, pages+1):
        html = fetch_search_page(query, page=p)
        if not html:
            continue
        offers = parse_search_results(html)
        logger.info("Page %d : %d offres trouvées", p, len(offers))
        for offer in offers:
            lang = lang_pref
            offer['cover_letter'] = generate_cover_letter_gpt(offer, lang=lang)
            offer['cv_url'] = CV_FR_URL if lang=="fr" else CV_ES_URL
            upsert_offer_to_notion(offer)
            all_offers.append(offer)
            rand_sleep(1.0, 3.0)
        rand_sleep(2.0, 5.0)
    logger.info("Terminé. Total offres traitées : %d", len(all_offers))
    return all_offers

# CLI
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="HelloWork Job Applier (GPT + Notion)")
    parser.add_argument("--query", "-q", required=True, help="Mot-clé recherche emploi, ex: 'testeur logiciel'")
    parser.add_argument("--pages", "-p", type=int, default=1, help="Nombre de pages à scraper")
    parser.add_argument("--lang", choices=["fr","es"], default="fr", help="Langue CV/lettre")
    args = parser.parse_args()
    run(args.query, pages=args.pages, lang_pref=args.lang)
