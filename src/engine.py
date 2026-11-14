import os
import json
import math
import datetime
import requests
import numpy as np
from scipy.stats import poisson
from google import genai

# Environment Variables (Stored securely in GitHub Secrets)
FOOTBALL_API_KEY = os.environ.get("FOOTBALL_DATA_API_KEY", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

DATA_DIR = "data"
PREDICTIONS_FILE = os.path.join(DATA_DIR, "predictions.json")
HISTORY_FILE = os.path.join(DATA_DIR, "history.json")

# Free tier leagues on Football-Data.org
LEAGUES = {
    "PL": "Premier League",
    "PD": "La Liga",
    "SA": "Serie A",
    "BL1": "Bundesliga",
    "FL1": "Ligue 1",
    "CL": "UEFA Champions League"
}

def ensure_data_files():
    os.makedirs(DATA_DIR, exist_ok=True)
    if not os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "w") as f:
            json.dump({
                "stats": {
                    "total_picks": 0,
                    "wins": 0,
                    "losses": 0,
                    "win_rate": 0.0,
                    "roi_units": 0.0
                },
                "graded_matches": []
            }, f, indent=2)

def fetch_matches(date_from, date_to, status="SCHEDULED"):
    """Fetches matches from Football-Data.org free API."""
    if not FOOTBALL_API_KEY:
        print("Warning: FOOTBALL_DATA_API_KEY not set. Using fallback sample fixtures.")
        return get_fallback_fixtures()

    url = f"https://api.football-data.org/v4/matches?dateFrom={date_from}&dateTo={date_to}&status={status}"
    headers = {"X-Auth-Token": FOOTBALL_API_KEY}
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        if resp.status_code == 200:
            matches = resp.json().get("matches", [])
            return [m for m in matches if m.get("competition", {}).get("code") in LEAGUES]
        else:
            print(f"API Error {resp.status_code}: {resp.text}")
    except Exception as e:
        print(f"Fetch error: {e}")
    return []

def calculate_poisson_model(home_attack, home_defense, away_attack, away_defense):
    """Generates 1X2, Over/Under 2.5, BTTS, and Projected Score using Bivariate Poisson."""
    avg_home_goals = 1.48
    avg_away_goals = 1.18

    lambda_home = max(0.35, min(4.2, home_attack * away_defense * avg_home_goals))
    lambda_away = max(0.25, min(3.8, away_attack * home_defense * avg_away_goals))

    max_goals = 6
    matrix = np.zeros((max_goals, max_goals))
    for h in range(max_goals):
        for a in range(max_goals):
            matrix[h][a] = poisson.pmf(h, lambda_home) * poisson.pmf(a, lambda_away)

    prob_home = float(np.sum(np.tril(matrix, -1)))
    prob_draw = float(np.sum(np.diag(matrix)))
    prob_away = float(np.sum(np.triu(matrix, 1)))

    # Normalize 1X2 probabilities
    total_1x2 = prob_home + prob_draw + prob_away
    prob_home, prob_draw, prob_away = prob_home / total_1x2, prob_draw / total_1x2, prob_away / total_1x2

    # Over 2.5 Goals
    prob_under_25 = sum(matrix[h][a] for h in range(max_goals) for a in range(max_goals) if h + a <= 2)
    prob_over_25 = 1.0 - prob_under_25

    # Both Teams To Score (BTTS)
    prob_btts = sum(matrix[h][a] for h in range(1, max_goals) for a in range(1, max_goals))

    # Most likely exact scoreline
    best_idx = np.unravel_index(np.argmax(matrix, axis=None), matrix.shape)
    projected_score = f"{best_idx[0]}-{best_idx[1]}"

    return {
        "home_win_prob": round(prob_home * 100, 1),
        "draw_prob": round(prob_draw * 100, 1),
        "away_win_prob": round(prob_away * 100, 1),
        "over_25_prob": round(prob_over_25 * 100, 1),
        "btts_prob": round(prob_btts * 100, 1),
        "projected_score": projected_score,
        "xg_home": round(lambda_home, 2),
        "xg_away": round(lambda_away, 2)
    }

def derive_team_strengths(home_name, away_name):
    """Deterministic Elo-proxy strength generator based on club metrics when full season table isn't queried."""
    top_tier = ["Manchester City", "Arsenal", "Liverpool", "Real Madrid", "FC Barcelona", "Bayern", "Inter", "Paris Saint-Germain", "Bayer Leverkusen"]
    mid_tier = ["Aston Villa", "Tottenham", "Chelsea", "Newcastle", "Atletico", "Juventus", "AC Milan", "Dortmund", "RB Leipzig", "Atalanta"]

    h_att, h_def = 1.05, 0.95
    a_att, a_def = 1.00, 1.00

    if any(t.lower() in home_name.lower() for t in top_tier):
        h_att, h_def = 1.38, 0.72
    elif any(t.lower() in home_name.lower() for t in mid_tier):
        h_att, h_def = 1.18, 0.86

    if any(t.lower() in away_name.lower() for t in top_tier):
        a_att, a_def = 1.34, 0.75
    elif any(t.lower() in away_name.lower() for t in mid_tier):
        a_att, a_def = 1.14, 0.89

    return h_att, h_def, a_att, a_def

def get_gemini_consensus(match_info, stats):
    """Uses Gemini Free Tier to evaluate stats, assign risk tier, and generate match insight."""
    probs = {
        f"{match_info['home']} Win": stats["home_win_prob"],
        "Draw": stats["draw_prob"],
        f"{match_info['away']} Win": stats["away_win_prob"]
    }
    best_pick = max(probs, key=probs.get)
    best_prob = probs[best_pick]

    # Fair implied odds + simulated market margin for Value Bet detection
    fair_odds = round(100.0 / max(best_prob, 10.0), 2)
    market_odds = round(fair_odds * 1.06, 2) if best_prob > 58 else round(fair_odds * 0.96, 2)
    is_value = market_odds > fair_odds

    if best_prob >= 62:
        risk_tier = "Safe"
    elif best_prob >= 46:
        risk_tier = "Balanced"
    else:
        risk_tier = "High-Risk"

    analysis = f"Poisson xG models project {stats['xg_home']} vs {stats['xg_away']} goals, giving {best_pick} a {best_prob}% probability edge."

    if GEMINI_API_KEY:
        try:
            client = genai.Client(api_key=GEMINI_API_KEY)
            prompt = (
                f"You are LimSoccer AI Analyst. Match: {match_info['home']} vs {match_info['away']} ({match_info['league']}). "
                f"Poisson Stats: Home {stats['home_win_prob']}%, Draw {stats['draw_prob']}%, Away {stats['away_win_prob']}%, "
                f"Over 2.5: {stats['over_25_prob']}%, BTTS: {stats['btts_prob']}%, xG: {stats['xg_home']}-{stats['xg_away']}. "
                f"Write a sharp, objective 2-sentence match preview explaining why {best_pick} ({risk_tier} tier) is favored. No markdown."
            )
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt
            )
            if response and response.text:
                analysis = response.text.strip().replace("\n", " ")
        except Exception as e:
            print(f"Gemini fallback triggered: {e}")

    return {
        "pick": best_pick,
        "confidence": best_prob,
        "risk_tier": risk_tier,
        "fair_odds": fair_odds,
        "market_odds": market_odds,
        "is_value_bet": is_value,
        "ai_preview": analysis
    }

def grade_yesterday_predictions():
    """Grades previous day's predictions against finished match scores."""
    if not os.path.exists(PREDICTIONS_FILE) or not os.path.exists(HISTORY_FILE):
        return

    with open(PREDICTIONS_FILE, "r") as f:
        old_data = json.load(f)
    with open(HISTORY_FILE, "r") as f:
        history = json.load(f)

    yesterday = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
    today = datetime.date.today().isoformat()
    finished = fetch_matches(yesterday, today, status="FINISHED")

    if not finished:
        return

    results_map = {}
    for m in finished:
        ft = m.get("score", {}).get("fullTime", {})
        if ft.get("home") is not None and ft.get("away") is not None:
            results_map[str(m["id"])] = (ft["home"], ft["away"], m.get("score", {}).get("winner"))

    graded_ids = {str(g["id"]) for g in history.get("graded_matches", [])}

    for pred in old_data.get("matches", []):
        mid = str(pred["id"])
        if mid in results_map and mid not in graded_ids:
            h_goals, a_goals, winner = results_map[mid]
            actual_outcome = f"{pred['home']} Win" if winner == "HOME_TEAM" else (f"{pred['away']} Win" if winner == "AWAY_TEAM" else "Draw")
            won = (pred["consensus"]["pick"] == actual_outcome)
            profit = round(pred["consensus"]["market_odds"] - 1.0, 2) if won else -1.0

            history["stats"]["total_picks"] += 1
            if won:
                history["stats"]["wins"] += 1
            else:
                history["stats"]["losses"] += 1
            history["stats"]["roi_units"] = round(history["stats"]["roi_units"] + profit, 2)
            history["stats"]["win_rate"] = round((history["stats"]["wins"] / history["stats"]["total_picks"]) * 100, 1)

            history["graded_matches"].insert(0, {
                "id": mid,
                "date": pred["date"],
                "league": pred["league"],
                "match": f"{pred['home']} vs {pred['away']}",
                "pick": pred["consensus"]["pick"],
                "odds": pred["consensus"]["market_odds"],
                "actual_score": f"{h_goals}-{a_goals}",
                "status": "WON" if won else "LOST",
                "profit_units": profit
            })

    history["graded_matches"] = history["graded_matches"][:100]
    with open(HISTORY_FILE, "w") as f:
        json.dump(history, f, indent=2)

def get_fallback_fixtures():
    """Provides sample fixtures if API key is not configured yet."""
    today = datetime.date.today().isoformat()
    tomorrow = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
    return [
        {"id": 9001, "utcDate": f"{today}T16:00:00Z", "competition": {"code": "PL", "name": "Premier League"}, "homeTeam": {"name": "Arsenal"}, "awayTeam": {"name": "Chelsea"}},
        {"id": 9002, "utcDate": f"{today}T18:30:00Z", "competition": {"code": "PD", "name": "La Liga"}, "homeTeam": {"name": "Real Madrid"}, "awayTeam": {"name": "Atletico Madrid"}},

{"id": 9003, "utcDate": f"{tomorrow}T15:00:00Z", "competition": {"code": "SA", "name": "Serie A"}, "homeTeam": {"name": "Inter"}, "awayTeam": {"name": "AC Milan"}},
{"id": 9004, "utcDate": f"{tomorrow}T17:30:00Z", "competition": {"code": "BL1", "name": "Bundesliga"}, "homeTeam": {"name": "Bayern Munich"}, "awayTeam": {"name": "Dortmund"}}
]
def run_pipeline():
ensure_data_files()
grade_yesterday_predictions()
today = datetime.date.today()
week_end = today + datetime.timedelta(days=7)
raw_matches = fetch_matches(today.isoformat(), week_end.isoformat(), status="SCHEDULED")
if not raw_matches:
raw_matches = get_fallback_fixtures()
processed_matches = []
for m in raw_matches[:40]:
home = m["homeTeam"]["name"]
away = m["awayTeam"]["name"]
league = m["competition"]["name"]
utc_date = m["utcDate"]
match_date = utc_date.split("T")[0]
h_att, h_def, a_att, a_def = derive_team_strengths(home, away)
stats = calculate_poisson_model(h_att, h_def, a_att, a_def)
info = {"id": m["id"], "home": home, "away": away, "league": league, "date": match_date, "kickoff_utc": utc_date}
consensus = get_gemini_consensus(info, stats)
processed_matches.append({
**info,
"is_today": (match_date == today.isoformat()),
"stats": stats,
"consensus": consensus
})
output = {
"updated_at": datetime.datetime.utcnow().isoformat() + "Z",
"today_date": today.isoformat(),
"total_matches": len(processed_matches),
"matches": processed_matches
}
with open(PREDICTIONS_FILE, "w") as f:
json.dump(output, f, indent=2)
print(f"Successfully generated {len(processed_matches)} predictions.")
if __name__ == "__main__":
run_pipeline()
