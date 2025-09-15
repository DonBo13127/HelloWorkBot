# notion_client.py
import os
import requests
from typing import Dict, Optional, List

NOTION_TOKEN = os.environ.get("NOTION_TOKEN")
DATABASE_ID = os.environ.get("NOTION_DATABASE_ID")
NOTION_VERSION = "2022-06-28"
HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Notion-Version": NOTION_VERSION,
    "Content-Type": "application/json",
}

if not NOTION_TOKEN or not DATABASE_ID:
    raise SystemExit("Veuillez définir NOTION_TOKEN et NOTION_DATABASE_ID dans les variables d'environnement.")

def query_database_by_link_or_title(link: Optional[str]=None, title: Optional[str]=None) -> List[Dict]:
    """
    Interroge la base Notion pour voir si une page avec le même link ou title existe déjà.
    Retourne la liste des pages trouvées (peut être vide).
    """
    url = f"https://api.notion.com/v1/databases/{DATABASE_ID}/query"
    filters = []
    if link:
        filters.append({
            "property": "Link",
            "url": {
                "equals": link
            }
        })
    if title:
        # Title filter must use rich_text or title depending on property; we try "Title" (title type)
        filters.append({
            "property": "Title",
            "title": {
                "contains": title
            }
        })
    if not filters:
        return []

    # If both provided, search OR them
    if len(filters) == 1:
        payload = {"filter": filters[0]}
    else:
        payload = {"filter": {"or": filters}}

    resp = requests.post(url, headers=HEADERS, json=payload, timeout=20)
    resp.raise_for_status()
    data = resp.json()
    return data.get("results", [])


def create_page(properties: Dict, children: Optional[List[Dict]] = None) -> Dict:
    """
    Crée une page dans la database Notion.
    properties : dictionnaire formatté selon l'API Notion.
    Exemple minimal :
    {
      "Title": {"title":[{"type":"text","text":{"content":"Mon titre"}}]},
      "Company": {"rich_text":[{"type":"text","text":{"content":"ACME"}}]},
      "Link": {"url":"https://..."},
      "Date": {"date":{"start":"2025-09-15"}}
    }
    """
    url = "https://api.notion.com/v1/pages"
    payload = {
        "parent": {"database_id": DATABASE_ID},
        "properties": properties
    }
    if children:
        payload["children"] = children

    resp = requests.post(url, headers=HEADERS, json=payload, timeout=20)
    resp.raise_for_status()
    return resp.json()


def build_properties_from_offer(offer: Dict) -> Dict:
    """
    Convertit une offre (title, company, location, date, link, source) en propriétés Notion.
    Adapte selon ta DB Notion (noms des colonnes). Si ta DB a d'autres noms, change ici.
    """
    title = offer.get("title") or "Sans titre"
    company = offer.get("company") or ""
    location = offer.get("location") or ""
    date = offer.get("date") or None
    link = offer.get("link") or None
    source = offer.get("source") or "scraper"

    props = {}

    # Title (champ Notion type title — souvent nommé "Title")
    props["Title"] = {"title": [{"type": "text", "text": {"content": title}}]}

    # Company (rich_text)
    props["Company"] = {"rich_text": [{"type": "text", "text": {"content": company}}]}

    # Location
    props["Location"] = {"rich_text": [{"type": "text", "text": {"content": location}}]}

    # Link (url) - property must be of type URL in the Notion db
    if link:
        props["Link"] = {"url": link}

    # Date - try to set as date if present and parseable (string YYYY-MM-DD or similar)
    if date:
        # Notion accepts full ISO 8601 date string; we won't parse complex formats here.
        props["Date"] = {"date": {"start": date}}

    # Source (select) - will work if the property is of type "select" or "multi_select"
    props["Source"] = {"select": {"name": source}}

    return props


def upsert_offer_to_notion(offer: Dict) -> Dict:
    """
    Vérifie doublon (link/title). Si absent -> crée la page. Retourne le résultat Notion ou l'objet existant.
    """
    link = offer.get("link")
    title = offer.get("title")
    found = query_database_by_link_or_title(link=link, title=title)
    if found:
        return {"status": "exists", "notion_result": found[0]}

    props = build_properties_from_offer(offer)
    result = create_page(properties=props)
    return {"status": "created", "notion_result": result}
