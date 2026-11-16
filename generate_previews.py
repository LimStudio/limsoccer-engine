import os
import requests
from google.genai import types

# Environment Secrets & Configurations
FOOTBALL_API_KEY = os.environ.get("FOOTBALL_API_KEY", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
BLOGGER_BLOG_ID = os.environ.get("BLOGGER_BLOG_ID", "")
CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
REFRESH_TOKEN = os.environ.get("GOOGLE_REFRESH_TOKEN", "")

# API-Football v3 Endpoints & Headers (Free Tier - 100 req/day)
RAPID_API_HOST = "v3.football.api-sports.io"
HEADERS = {
    "x-rapidapi-key": FOOTBALL_API_KEY,
    "x-rapidapi-host": RAPID_API_HOST
}

# Top league IDs supported or prioritized
LEAGUE_IDS = [39, 140, 78, 61, 135, 2] # Premier League, La Liga, Bundesliga, Ligue 1, Serie A, Champions League

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

def fetch_todays_fixtures(date_str):
    """Fetches fixtures and predictions from API-Football for today."""
    url = f"https://{RAPID_API_HOST}/fixtures"
    params = {"date": date_str}
    
    response = requests.get(url, headers=HEADERS, params=params, timeout=15)
    if response.status_code == 200:
        data = response.json()
        return data.get("response", [])
    else:
        print(f"Error fetching fixtures: {response.status_code} - {response.text}")
        return []

def calculate_match_models(fixture_data):
    """Calculates Poisson & Win/Draw/Win percentages based on fixture stats or odds."""
    # Placeholder for your custom Poisson / Elo calculation engine
    home_team = fixture_data['teams']['home']['name']
    away_team = fixture_data['teams']['away']['name']
    
    return {
        "home_team": home_team,
        "away_team": away_team,
        "home_win_prob": "48%",
        "draw_prob": "28%",
        "away_win_prob": "24%",
        "predicted_score": "2 - 1"
    }

def publish_to_blogger(title, content):
    """Publishes a draft article directly to your Blogger site."""
    access_token = get_oauth_access_token()
    url = f"https://www.googleapis.com/blogger/v3/blogs/{BLOGGER_BLOG_ID}/posts/"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    body = {
        "title": title,
        "content": content,
        "isDraft": True  # Saves as draft for your review or automated pipeline
    }
    response = requests.post(url, headers=headers, json=body, timeout=15)
    if response.status_code == 200:
        print(f"Successfully published draft: {title}")
    else:
        print(f"Failed to publish draft: {response.status_code} - {response.text}")

if __name__ == "__main__":
    import datetime
    today = datetime.datetime.utcnow().strftime("%Y-%m-%d")
    print(f"Running LIM SOCCER AI Engine for fixtures on: {today}")
    
    fixtures = fetch_todays_fixtures(today)
    print(f"Fetched {len(fixtures)} total fixtures from API-Football.")
    
    # Process top matches to conserve your 100 requests/day limit
    for fixture in fixtures[:5]: 
        league_id = fixture['league']['id']
        if league_id in LEAGUE_IDS:
            model = calculate_match_models(fixture)
            print(f"Generated model for {model['home_team']} vs {model['away_team']}")
