import os
import json
import time
import datetime
import urllib.parse
import requests

# Environment Variables (Stored securely in GitHub Secrets)
GOOGLE_SHEET_WEBAPP_URL = os.environ.get("GOOGLE_SHEET_WEBAPP_URL", "")
GOOGLE_SHEET_SECRET = os.environ.get("GOOGLE_SHEET_SECRET", "LIMSOCCER_SECRET_2026")

# Free WhatsApp Delivery Options (Supports Meta WhatsApp Cloud API Free Tier OR CallMeBot)
WHATSAPP_PROVIDER = os.environ.get("WHATSAPP_PROVIDER", "META_FREE")  # "META_FREE" or "CALLMEBOT"
META_WA_TOKEN = os.environ.get("META_WA_TOKEN", "")
META_WA_PHONE_ID = os.environ.get("META_WA_PHONE_ID", "")
CALLMEBOT_API_KEY = os.environ.get("CALLMEBOT_API_KEY", "")

PREDICTIONS_FILE = os.path.join("data", "predictions.json")
SITE_URL = "https://blogspot.com"


def load_predictions():
    if not os.path.exists(PREDICTIONS_FILE):
        print("No predictions.json found.")
        return None
    with open(PREDICTIONS_FILE, "r") as f:
        return json.load(f)


def fetch_active_subscribers():
    """Fetches active WhatsApp subscribers from your free Google Sheets Web App."""
    if not GOOGLE_SHEET_WEBAPP_URL:
        print("GOOGLE_SHEET_WEBAPP_URL not set. Skipping WhatsApp dispatch.")
        return []

    try:
        resp = requests.get(
            GOOGLE_SHEET_WEBAPP_URL,
            params={"token": GOOGLE_SHEET_SECRET},
            timeout=15
        )
        if resp.status_code == 200:
            data = resp.json()
            if data.get("status") == "success":
                return data.get("subscribers", [])
        print(f"Failed to fetch subscribers: {resp.text}")
    except Exception as e:
        print(f"Error connecting to Google Sheets backend: {e}")
    return []


def format_daily_digest(matches, today_str):
    """Creates a clean WhatsApp message for today's top predictions."""
    todays_matches = [m for m in matches if m.get("is_today")]
    if not todays_matches:
        todays_matches = matches[:5]

    # Sort by highest AI confidence
    top_picks = sorted(todays_matches, key=lambda x: x["consensus"]["confidence"], reverse=True)[:6]

    lines = [
        f"⚽ *LIMSOCCER DAILY PICKS ({today_str})* ⚽",
        "AI + Poisson Statistical Consensus:\n"
    ]

    for m in top_picks:
        c = m["consensus"]
        s = m["stats"]
        badge = "🟢" if c["risk_tier"] == "Safe" else ("🟡" if c["risk_tier"] == "Balanced" else "🔴")
        val_tag = " 💎VALUE" if c.get("is_value_bet") else ""
        lines.append(
            f"{badge} *{m['home']} vs {m['away']}* ({m['league']})\n"
            f"   Pick: *{c['pick']}* ({c['confidence']}%) | Score: {s['projected_score']}{val_tag}"
        )

    lines.append(f"\n📊 Full stats, Bet Slip Builder & Live ROI:\n👉 {SITE_URL}")
    return "\n".join(lines)


def format_weekly_digest(matches, today_str):
    """Creates a Monday Weekly Accumulator & Value Bet digest."""
    # Filter top Safe & Value picks across the next 7 days
    safe_picks = [
        m for m in matches
        if m["consensus"]["risk_tier"] in ("Safe", "Balanced")
    ]
    top_weekly = sorted(safe_picks, key=lambda x: x["consensus"]["confidence"], reverse=True)[:8]

    lines = [
        f"🏆 *LIMSOCCER WEEKLY VIP SLIP (Week of {today_str})* 🏆",
        "Top 7-Day Statistical & AI Consensus Picks:\n"
    ]

    for m in top_weekly:
        c = m["consensus"]
        lines.append(
            f"📅 {m['date']} | *{m['home']} vs {m['away']}*\n"
            f"   👉 *{c['pick']}* (Conf: {c['confidence']}% | Odds: ~{c['market_odds']})"
        )

    lines.append(f"\n🎥 Watch our weekly breakdown on @LimSoccerTV & build your slip:\n👉 {SITE_URL}")
    return "\n".join(lines)


def send_whatsapp_message(phone, text_body):
    """Sends a WhatsApp message using Meta Cloud API (Free Tier) or CallMeBot."""
    clean_phone = phone.replace("+", "").replace(" ", "")

    if WHATSAPP_PROVIDER == "META_FREE" and META_WA_TOKEN and META_WA_PHONE_ID:
        url = f"https://facebook.com{META_WA_PHONE_ID}/messages"
        headers = {
            "Authorization": f"Bearer {META_WA_TOKEN}",
            "Content-Type": "application/json"
        }
        payload = {
            "messaging_product": "whatsapp",
            "to": clean_phone,
            "type": "text",
            "text": {"preview_url": True, "body": text_body}
        }
        try:
            r = requests.post(url, headers=headers, json=payload, timeout=15)
            return r.status_code in (200, 201)
        except Exception as e:
            print(f"Meta API send error for {clean_phone}: {e}")
            return False

    elif WHATSAPP_PROVIDER == "CALLMEBOT" and CALLMEBOT_API_KEY:
        encoded_msg = urllib.parse.quote(text_body)
        url = f"https://callmebot.com{clean_phone}&text={encoded_msg}&apikey={CALLMEBOT_API_KEY}"
        try:
            r = requests.get(url, timeout=15)
            return r.status_code == 200
        except Exception as e:
            print(f"CallMeBot send error for {clean_phone}: {e}")
            return False

    else:
        print(f"[DRY RUN] Would send to {phone}:\n{text_body[:120]}...\n")
        return True


def run_notifications():
    data = load_predictions()
    if not data or not data.get("matches"):
        print("No matches available to broadcast.")
        return

    subscribers = fetch_active_subscribers()
    if not subscribers:
        print("No active subscribers found.")
        return

    today = datetime.date.today()
    today_str = today.isoformat()
    is_monday = (today.weekday() == 0)  # 0 = Monday

    daily_msg = format_daily_digest(data["matches"], today_str)
    weekly_msg = format_weekly_digest(data["matches"], today_str) if is_monday else ""

    sent_count = 0
    for sub in subscribers:
        phone = sub.get("phone", "")
        freq = sub.get("frequency", "Daily")

        if not phone:
            continue

        if freq == "Daily":
            if send_whatsapp_message(phone, daily_msg):
                sent_count += 1
            time.sleep(1.2)  # Rate-limit protection

        elif freq == "Weekly" and is_monday:
            if send_whatsapp_message(phone, weekly_msg):
                sent_count += 1
            time.sleep(1.2)

    print(f"WhatsApp broadcast complete. Sent {sent_count} messages.")


if __name__ == "__main__":
    run_notifications()
