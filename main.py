# main.py
"""
Production-ready multi-mode job applier for HelloWork (ethical).

Modes:
- ATS API mode: if HELLOWORK_API_USER & HELLOWORK_API_PASS are set AND DRY_RUN=false -> try to send application via HelloWork ATS Partner API.
- Prepare-only mode: prepare application package (cover letter, CV link) and upsert to Notion for manual validation.

Requirements:
pip install requests beautifulsoup4 lxml python-dotenv openai

ENV (example):
NOTION_TOKEN, NOTION_DATABASE_ID,
CV_FR_URL, CV_ES_URL,
OPENAI_API_KEY (optional),
HELLOWORK_API_USER, HELLOWORK_API_PASS (optional, for ATS API),
HELLOWORK_API_BASE (optional, default to ATS doc base),
DRY_RUN=true|false
"""

import os
import sys
import time
import json
import logging
import random
from typing import List, Dict, Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

# Helpers from your repo (ensure these files exist)
from utils import rand_sleep, save_json, append_json, check_robots_allowed, DEFAULT_USER_AGENT
from notion_client import upsert_offer_to_notion, build_properties_from_offer

# OpenAI optional
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
if OPENAI_API_KEY:
    import openai
    openai.api_key = OPENAI_API_KEY

# Config
NOTION_TOKEN = os.environ.get("NOTION_TOKEN")
NOTION_DATABASE_ID = os.environ.get("NOTION_DATABASE_ID")
CV_FR_URL = os.environ.get("CV_FR_URL")
CV_ES_URL = os.environ.get("CV_ES_URL")
USER_AGENT = os.environ.get("DEFAULT_USER_AGENT", DEFAULT_USER_AGENT)
DRY_RUN = os.environ.get("DRY_RUN", "true").lower() in ("1", "true", "yes")

HELLOWORK_API_USER = os.environ.get("HELLOWORK_API_USER")
HELLOWORK_API_PASS = os.environ.get("HELLOWORK_API_PASS")
HELLOWORK_API_BASE = os.environ.get("HELLOWORK_API_BASE", "https://ats-partner.hellowork.com/v1/api")

# Logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", handlers=[logging.StreamHandler(sys.stdout)])
logger = logging.getLogger("job-applier")

# HelloWork search defaults
BASE_DOMAIN = "https://www.hellowork.com"
SEARCH_PATH = "/fr-fr/recherche-emploi/"

session = requests.Session()
session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "fr-FR,fr;q=0.9"})

def fetch_search_page(query: str, page: int = 1) -> Optional[str]:
    url = urljoin(BASE_DOMAIN, SEARCH_PATH)
    params = {"q": query, "page": page}
    try:
        r = session.get(url, params=params, timeout=20)
        r.raise_for_status()
        return r.text
    except Exception as e:
        logger.error("Erreur HTTP fetch_search_page: %s", e)
        return None

def parse_search_results(html: str, base_url: str = BASE_DOMAIN) -> List[Dict]:
    soup = BeautifulSoup(html, "lxml")
    items = []
    # selectors generic
    offers = soup.select("article, .job-item, .result-item, .results__item")
    if not offers:
        offers = soup.select(".listing-item, .annonce, .offer, .job-card")
    for off in offers:
        title_tag = off.select_one("h2 a, a.job-title, a.offer-title, .job-title a")
        company_tag = off.select_one(".company, .job-company, .offer-company, .company-name")
        location_tag = off.select_one(".locality, .job-location, .offer-location")
        date_tag = off.select_one(".date, .job-date, .offer-date")
        link = None
        if title_tag and title_tag.has_attr("href"):
            link = urljoin(base_url, title_tag["href"])
        title = title_tag.get_text(strip=True) if title_tag else None
        company = company_tag.get_text(strip=True) if company_tag else None
        location = location_tag.get_text(strip=True) if location_tag else None
        date = date_tag.get_text(strip=True) if date_tag else None
        if title:
            items.append({"title": title, "company": company, "location": location, "date": date, "link": link, "source": "hellowork"})
    return items

def generate_cover_letter_basic(offer: Dict, lang: str = "fr") -> str:
    """
    Generate a simple cover letter. If OPENAI_API_KEY is provided, produce a nicer letter via OpenAI.
    """
    if OPENAI_API_KEY:
        prompt = f"Rédige une courte lettre de motivation en { 'français' if lang=='fr' else 'espagnol' } pour postuler au poste '{offer.get('title')}' chez {offer.get('company')}. Court, 6-8 phrases, ton professionnel."
        try:
            resp = openai.ChatCompletion.create(
                model="gpt-4o-mini" if hasattr(openai, "ChatCompletion") else "gpt-4o",
                messages=[{"role":"user","content":prompt}],
                max_tokens=300,
                temperature=0.7,
            )
            # compatibility guard for different OpenAI wrappers
            if isinstance(resp, dict):
                content = resp.get("choices", [])[0].get("message", {}).get("content", "")
            else:
                content = getattr(resp, "choices")[0].message.content
            return content.strip()
        except Exception as e:
            logger.warning("OpenAI erreur: %s — fallback to basic template.", e)
    # fallback simple template
    return (f"Bonjour,\n\nJe vous propose ma candidature pour le poste de {offer.get('title')} "
            f"au sein de {offer.get('company')}. Vous trouverez mon CV en pièce jointe ({CV_FR_URL if lang=='fr' else CV_ES_URL}).\n\nCordialement,\nYacine Bedhouche")

# --- ATS API integration (only works if HelloWork partner credentials are provided) ---
def hello_authenticate_api() -> Optional[str]:
    """
    Authenticate with HelloWork ATS API to retrieve a token (JWT).
    This function assumes the ATS API provides a login endpoint (see docs).
    You must have valid HELLOWORK_API_USER / HELLOWORK_API_PASS.
    Returns token string or None.
    """
    if not HELLOWORK_API_USER or not HELLOWORK_API_PASS:
        logger.info("HELLOWORK_API_USER/PASS not provided — skipping ATS API mode.")
        return None
    login_url = urljoin(HELLOWORK_API_BASE, "login-and-retrieve-token")
    payload = {"username": HELLOWORK_API_USER, "password": HELLOWORK_API_PASS}
    try:
        r = requests.post(login_url, json=payload, timeout=15)
        r.raise_for_status()
        data = r.json()
        token = data.get("token") or data.get("access_token") or data.get("jwt")
        if token:
            logger.info("Authentifié auprès de l'ATS HelloWork (token obtenu).")
            return token
        else:
            logger.error("Réponse d'auth sans token: %s", data)
            return None
    except Exception as e:
        logger.error("Erreur authentification ATS API: %s", e)
        return None

def ats_submit_application(token: str, offer: Dict, cover_text: str, cv_url: str) -> Dict:
    """
    Submits an application using HelloWork ATS API.
    The exact payload depends on HelloWork API contract — adapt fields accordingly.
    This function builds a safe generic payload often accepted by ATS APIs (applicant name, email, resume url, message).
    """
    submit_url = urljoin(HELLOWORK_API_BASE, "applications")  # may differ in real doc
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    payload = {
        "job_reference": offer.get("link"),   # placeholder: ATS likely expects job_id/reference
        "applicant": {
            "name": "Yacine Bedhouche",
            "email": os.environ.get("HELLOWORK_APPLICANT_EMAIL", os.environ.get("HELLOWORK_EMAIL")),
            "phone": os.environ.get("APPLICANT_PHONE", "")
        },
        "message": cover_text,
        "resume_url": cv_url,
        "source": "automation_script"
    }
    try:
        r = requests.post(submit_url, json=payload, headers=headers, timeout=20)
        r.raise_for_status()
        logger.info("Application envoyée via ATS API pour %s", offer.get("title"))
        return {"status": "submitted", "response": r.json()}
    except Exception as e:
        logger.error("Erreur envoi application ATS API: %s", e)
        return {"status": "error", "error": str(e), "status_code": getattr(e, 'response', None)}

# --- End ATS functions ---

def prepare_and_save_offer(offer: Dict, lang: str = "fr"):
    """
    1) Build cover letter
    2) Upsert to Notion (using notion_client.upsert_offer_to_notion) with cover_text and CV link stored in properties
    3) Save locally as fallback JSON
    """
    cover = generate_cover_letter_basic(offer, lang=lang)
    # add fields so Notion helper can map them
    offer_copy = dict(offer)
    offer_copy["cover_letter"] = cover
    offer_copy["cv_url"] = CV_FR_URL if lang == "fr" else CV_ES_URL
    # Notion upsert (uses build_properties_from_offer, modify if your DB columns differ)
    try:
        # augment properties mapping: ensure build_properties_from_offer includes Link, Date etc.
        res = upsert_offer_to_notion(offer_copy)
        logger.info("Notion upsert result: %s for %s", res.get("status"), offer.get("title"))
    except Exception as e:
        logger.warning("Erreur Notion upsert: %s — sauvegarde locale.", e)
        append_json(offer_copy)
    # local backup
    try:
        save_json([offer_copy], filename="data/last_prepared_offer.json")
    except Exception:
        logger.debug("Impossible d'écrire data backup.")

def run(query: str, pages: int = 1, lang_pref: str = "fr"):
    """
    Main runner: scrapes pages, for each offer either submits via ATS API (if available) or prepares for manual send.
    """
    # Respect robots.txt
    if not check_robots_allowed(BASE_DOMAIN, path=SEARCH_PATH):
        logger.error("robots.txt interdit le scraping sur %s%s — abort.", BASE_DOMAIN, SEARCH_PATH)
        return

    offers_collected = []
    token = None
    if HELLOWORK_API_USER and HELLOWORK_API_PASS and not DRY_RUN:
        token = hello_authenticate_api()
        if not token:
            logger.warning("Impossible d'obtenir token ATS — on bascule en mode prepare-only.")

    for p in range(1, pages + 1):
        html = fetch_search_page(query, page=p)
        if not html:
            continue
        items = parse_search_results(html)
        logger.info("Page %d: %d offers", p, len(items))
        for o in items:
            # basic dedupe: skip if no link
            if not o.get("link"):
                continue
            # Decide language for CV/letter based on location or user pref
            lang = lang_pref
            cover = generate_cover_letter_basic(o, lang=lang)

            if token and not DRY_RUN:
                # attempt ATS submit
                cv_url = CV_FR_URL if lang=="fr" else CV_ES_URL
                resp = ats_submit_application(token, o, cover, cv_url)
                if resp.get("status") == "submitted":
                    logger.info("ATS submit OK for %s", o.get("title"))
                else:
                    # fallback to prepare-only if API failed
                    logger.warning("ATS submit failed, prepare for manual: %s", resp.get("error"))
                    prepare_and_save_offer(o, lang=lang)
            else:
                # prepare-only -> save to Notion + local
                prepare_and_save_offer(o, lang=lang)

            offers_collected.append(o)
            # polite delay
            rand_sleep(1.0, 3.0)
        # page delay
        rand_sleep(2.0, 6.0)

    logger.info("Run complete. offers_total=%d", len(offers_collected))
    # final backup
    try:
        save_json(offers_collected, filename="data/all_offers.json")
    except Exception:
        logger.debug("Can't write full backup.")
    return offers_collected

# CLI entry
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="HelloWork apply runner (ATS if creds provided, else prepare-only)")
    parser.add_argument("--query", "-q", required=True, help="Search query e.g. 'testeur logiciel'")
    parser.add_argument("--pages", "-p", type=int, default=1, help="Pages to search")
    parser.add_argument("--lang", choices=["fr","es"], default="fr", help="Preferred language for cover letter/CV")
    args = parser.parse_args()
    run(args.query, pages=args.pages, lang_pref=args.lang)
