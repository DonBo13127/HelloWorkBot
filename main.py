# main.py
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
import sys
import argparse

from utils import (
    check_robots_allowed,
    rand_sleep,
    append_json,
    DEFAULT_USER_AGENT,
)

HEADERS = {"User-Agent": DEFAULT_USER_AGENT, "Accept-Language": "fr-FR,fr;q=0.9"}

def fetch(url, session, timeout=20):
    resp = session.get(url, timeout=timeout)
    resp.raise_for_status()
    return resp.text

def parse_hellowork_list(html, base_url="https://www.hellowork.com"):
    soup = BeautifulSoup(html, "lxml")
    results = []
    # Sélecteurs génériques - adapter si HelloWork change
    offers = soup.select("article, .job-item, .result-item")
    for off in offers:
        title_tag = off.select_one("h2 a, a.job-title, .job-title a")
        company_tag = off.select_one(".company, .job-company")
        location_tag = off.select_one(".locality, .job-location")
        date_tag = off.select_one(".date, .job-date")

        link = None
        if title_tag and title_tag.has_attr("href"):
            link = urljoin(base_url, title_tag['href'])

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
                "link": link
            })
    return results

def run_search(query, pages=1, base_url="https://www.hellowork.com/fr-fr/recherche-emploi/"):
    session = requests.Session()
    session.headers.update(HEADERS)

    # Vérification robots.txt pour la base
    if not check_robots_allowed(base_url, user_agent=DEFAULT_USER_AGENT, path="/"):
        print("Accès bloqué par robots.txt — arrêt par précaution.")
        return []

    all_offers = []
    for p in range(1, pages + 1):
        # Construire l'URL de recherche (s'adapter selon site)
        params = {"q": query, "page": p}
        try:
            resp = session.get(base_url, params=params, timeout=20)
            resp.raise_for_status()
        except Exception as e:
            print(f"Erreur HTTP page {p}: {e}")
            break

        offers = parse_hellowork_list(resp.text, base_url="https://www.hellowork.com")
        for o in offers:
            append_json(o)  # sauvegarde progressive
            all_offers.append(o)

        # pause avant la page suivante
        rand_sleep(2.0, 5.0)

    return all_offers

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scraper éthique HelloWork (exemple).")
    parser.add_argument("--query", "-q", required=True, help="Terme de recherche, ex: 'developpeur python'")
    parser.add_argument("--pages", "-p", type=int, default=1, help="Nombre de pages à parcourir")
    args = parser.parse_args()

    results = run_search(args.query, pages=args.pages)
    print(f"Offres récupérées: {len(results)}")
    for r in results:
        print(r)
