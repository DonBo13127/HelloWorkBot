# utils.py
import time
import random
import json
import os
from urllib.parse import urlparse
import urllib.robotparser

DEFAULT_USER_AGENT = "Mozilla/5.0 (compatible; YacineBot/1.0; +https://example.com/bot)"

def check_robots_allowed(base_url, user_agent=DEFAULT_USER_AGENT, path="/"):
    """
    Vérifie robots.txt du site et retourne True si l'accès au path est autorisé.
    """
    parsed = urlparse(base_url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    rp = urllib.robotparser.RobotFileParser()
    try:
        rp.set_url(robots_url)
        rp.read()
        return rp.can_fetch(user_agent, path)
    except Exception:
        # si impossible de récupérer robots.txt, on renvoie False par précaution
        return False

def rand_sleep(min_s=1.0, max_s=3.0):
    """Pause aléatoire pour imiter un rythme humain et éviter surcharger le site."""
    time.sleep(random.uniform(min_s, max_s))

def save_json(data, filename="data/results.json"):
    os.makedirs(os.path.dirname(filename), exist_ok=True)
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def append_json(item, filename="data/results.json"):
    os.makedirs(os.path.dirname(filename), exist_ok=True)
    all_data = []
    if os.path.exists(filename):
        try:
            with open(filename, "r", encoding="utf-8") as f:
                all_data = json.load(f)
        except Exception:
            all_data = []
    all_data.append(item)
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(all_data, f, ensure_ascii=False, indent=2)
