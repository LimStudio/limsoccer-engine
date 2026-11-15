import os
import json
import datetime
import requests
import numpy as np
from scipy.stats import poisson
from google import genai
from google.genai import types

# Environment Secrets & Configurations
FOOTBALL_API_KEY = os.environ.get("FOOTBALL_DATA_API_KEY", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
BLOGGER_BLOG_ID = os.environ.get("BLOGGER_BLOG_ID", "")
CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
REFRESH_TOKEN = os.environ.get("GOOGLE_REFRESH_TOKEN", "")

LEAGUE_CODES = ["PL", "PD", "SA", "BL1", "FL1", "CL"]

def get_oauth_access_token():
    """Renews and returns a Google OAuth2 access token using a persistent refresh token."""
    url = "https://oauth2.googleapis.com/token"
    payload = {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "refresh_token": REFRESH_TOKEN,
        "grant_type": "refresh_token"
    }
    resp = requests.post(url, data=payload, timeout=15)
    if resp.status_code == 200:
        return resp.json().get("access_token")
    raise Exception(f"OAuth token refresh failed ({resp.status_code}): {resp.text}")

def fetch_top_matches():
    """Fetches today's high-profile scheduled matches from api.football-data.org."""
    today = datetime.date.today().isoformat()
    url = f"https://api.football-data.org/v4/matches?dateFrom={today}&dateTo={today}"
    headers = {"X-Auth-Token": FOOTBALL_API_KEY} if FOOTBALL_API_KEY else {}
    
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        if resp.status_code == 200:
            matches = resp.json().get("matches", [])
            filtered = [m for m in matches if m.get("competition", {}).get("code") in LEAGUE_CODES]
            return filtered[:3] # Top 3 matches for daily previews
    except Exception as e:
        print(f"Fixture fetch exception: {e}")
    
    # Fallback sample match if API quota is reached
    return [{
        "id": 9999,
        "competition": {"name": "Premier League"},
        "homeTeam": {"name": "Arsenal"},
        "awayTeam": {"name": "Chelsea"},
        "utcDate": f"{today}T16:30:00Z"
    }]

def calculate_match_models(home_name, away_name):
    """Calculates xG, Poisson 1X2 probabilities, and fair implied odds at zero cost."""
    # Deterministic strength proxies
    top_clubs = ["Manchester City", "Arsenal", "Liverpool", "Real Madrid", "FC Barcelona", "Bayern Munich", "Inter"]
    h_att, h_def = (1.38, 0.72) if any(c in home_name for c in top_clubs) else (1.10, 0.90)
    a_att, a_def = (1.34, 0.75) if any(c in away_name for c in top_clubs) else (1.00, 0.98)

    lambda_home = max(0.4, min(4.0, h_att * a_def * 1.48))
    lambda_away = max(0.3, min(3.5, a_att * h_def * 1.18))

    matrix = np.zeros((6, 6))
    for h in range(6):
        for a in range(6):
            matrix[h][a] = poisson.pmf(h, lambda_home) * poisson.pmf(a, lambda_away)

    p_home = float(np.sum(np.tril(matrix, -1)))
    p_draw = float(np.sum(np.diag(matrix)))
    p_away = float(np.sum(np.triu(matrix, 1)))
    total = p_home + p_draw + p_away
    
    p_home, p_draw, p_away = p_home/total, p_draw/total, p_away/total

    return {
        "home_prob": round(p_home * 100, 1),
        "draw_prob": round(p_draw * 100, 1),
        "away_prob": round(p_away * 100, 1),
        "home_odds": round(100.0 / (p_home * 100), 2),
        "draw_odds": round(100.0 / (p_draw * 100), 2),
        "away_odds": round(100.0 / (p_away * 100), 2),
        "xg_home": round(lambda_home, 2),
        "xg_away": round(lambda_away, 2)
    }

def generate_seo_article(match_info, stats):
    """Uses Gemini 2.5 Flash to generate an 800-word AdSense-compliant HTML match preview."""
    client = genai.Client(api_key=GEMINI_API_KEY)
    
    home = match_info["homeTeam"]["name"]
    away = match_info["awayTeam"]["name"]
    league = match_info["competition"]["name"]

    prompt = f"""
    Write an exhaustive, 800-word SEO-friendly football match preview for {home} vs {away} in the {league}.
    Statistical Models Output:
    - Expected Goals (xG): {home} {stats['xg_home']} - {stats['xg_away']} {away}
    - Implied Probabilities: {home} Win ({stats['home_prob']}%), Draw ({stats['draw_prob']}%), {away} Win ({stats['away_prob']}%)
    - Fair Implied Odds: {home} (@{stats['home_odds']}), Draw (@{stats['draw_odds']}), {away} (@{stats['away_odds']})

    Strict Formatting Requirements:
    - Return clean semantic HTML only. Do NOT include <html>, <head>, or <body> tags.
    - Start with an engaging <h2> introduction outlining the fixture's context and title race or table implications.
    - Include subheadings (<h2>, <h3>) covering Team Form & Recent Head-to-Head, Tactical Key Battles, and Expected Goals Analysis.
    - Embed a responsive HTML comparison table showing Team Form, Elo Rating, Projected xG, and Implied Probability.
    - Conclude with an explicit, bolded Responsible Analytics Disclaimer (18+) stating that statistical projections are for informational and educational purposes only.
    - Tone must be professional, objective, analytical, and fully compliant with Google AdSense quality guidelines.
    """

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt
    )
    return response.text.strip()

def upload_to_blogger_draft(title, html_content, league_name):
    """Uploads the generated article to Blogger API v3 as a Draft (isDraft=True)."""
    access_token = get_oauth_access_token()
    url = f"https://www.googleapis.com/blogger/v3/blogs/{BLOGGER_BLOG_ID}/posts"
    
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "title": title,
        "content": html_content,
        "labels": ["Match Previews", "Statistical Analysis", league_name],
        "isDraft": True
    }

    resp = requests.post(url, headers=headers, json=payload, timeout=15)
    if resp.status_code in (200, 201):
        print(f"Successfully uploaded draft: {title}")
    else:
        print(f"Blogger API upload error ({resp.status_code}): {resp.text}")

def main():
    if not BLOGGER_BLOG_ID or not CLIENT_ID:
        print("Missing Blogger OAuth credentials or Blog ID. Exiting.")
        return

    matches = fetch_top_matches()
    print(f"Found {len(matches)} fixtures to process.")

    for m in matches:
        home = m["homeTeam"]["name"]
        away = m["awayTeam"]["name"]
        league = m["competition"]["name"]
        title = f"{home} vs {away} Prediction, xG & Tactical Preview ({datetime.date.today().isoformat()})"
        
        print(f"Processing: {title}")
        stats = calculate_match_models(home, away)
        html_body = generate_seo_article(m, stats)
        upload_to_blogger_draft(title, html_body, league)

if __name__ == "__main__":
    main()
