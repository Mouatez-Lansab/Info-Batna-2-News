import os
import re
import json
import requests
from bs4 import BeautifulSoup

# ---- Config (from GitHub Actions secrets / env vars) ----
BOT_TOKEN = os.environ["BOT_TOKEN"]
CHANNEL_USERNAME = os.environ["CHANNEL_USERNAME"]  # e.g. "@scmi_batna2_news"
NEWS_URL = "https://sc-mi.univ-batna2.dz/news"
LAST_SEEN_FILE = "last_seen.json"

SEND_MESSAGE_API = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
SEND_DOCUMENT_API = f"https://api.telegram.org/bot{BOT_TOKEN}/sendDocument"

DOCUMENT_EXTENSIONS = (".pdf", ".doc", ".docx", ".xls", ".xlsx", ".zip", ".rar")


def to_drive_direct_link(url):
    """Convert a Google Drive 'view' share link into a direct-download link
    that Telegram's servers can fetch. Returns None if it's not a Drive link
    we recognize."""
    match = re.search(r"drive\.google\.com/file/d/([a-zA-Z0-9_-]+)", url)
    if not match:
        match = re.search(r"drive\.google\.com/open\?id=([a-zA-Z0-9_-]+)", url)
    if not match:
        match = re.search(r"[?&]id=([a-zA-Z0-9_-]+)", url) if "drive.google.com" in url else None
    if match:
        file_id = match.group(1)
        return f"https://drive.google.com/uc?export=download&id={file_id}"
    return None


def find_attachment(article_url):
    """Open a news article page and look for an attached document:
    a direct link to a PDF/Office/zip file, or a Google Drive link.
    Returns a direct-downloadable URL, or None if nothing relevant is found."""
    try:
        resp = requests.get(article_url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
    except requests.RequestException as e:
        print("Impossible d'ouvrir l'article pour chercher une pièce jointe :", e)
        return None

    soup = BeautifulSoup(resp.text, "html.parser")

    for a in soup.select("a[href]"):
        href = a.get("href", "")
        if href.startswith("/"):
            href = "https://sc-mi.univ-batna2.dz" + href

        # Direct document file (pdf, docx, etc.)
        if href.lower().split("?")[0].endswith(DOCUMENT_EXTENSIONS):
            return href

        # Google Drive share link
        if "drive.google.com" in href:
            direct = to_drive_direct_link(href)
            if direct:
                return direct

    return None


def fetch_news():
    """Scrape the news listing page and return a list of {title, url, date} dicts,
    newest first, in the order they appear on the page."""
    resp = requests.get(NEWS_URL, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    items = []
    # News titles are <h2><a href="...">Title</a></h2> or similar on OpenScholar sites.
    # We look for links pointing to /news/... which is the article pattern.
    for a in soup.select("a[href*='/news/']"):
        href = a.get("href", "")
        title = a.get_text(strip=True)
        if not title or "/news/" not in href:
            continue
        if href.startswith("/"):
            href = "https://sc-mi.univ-batna2.dz" + href
        # Skip duplicate/empty or the generic "Read more about ..." links duplicate titles;
        # keep the first occurrence per url.
        items.append({"title": title, "url": href})

    # De-duplicate by url, keep first occurrence (page order = newest first)
    seen_urls = set()
    unique_items = []
    for item in items:
        if item["url"] not in seen_urls:
            seen_urls.add(item["url"])
            unique_items.append(item)

    return unique_items


def load_last_seen():
    if os.path.exists(LAST_SEEN_FILE):
        with open(LAST_SEEN_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def save_last_seen(urls):
    with open(LAST_SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(urls, f, ensure_ascii=False, indent=2)


def send_telegram_message(text):
    resp = requests.post(
        SEND_MESSAGE_API,
        data={
            "chat_id": CHANNEL_USERNAME,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
        },
        timeout=30,
    )
    if not resp.ok:
        print("Telegram error:", resp.status_code, resp.text)
    resp.raise_for_status()


def send_telegram_document(document_url, caption):
    """Ask Telegram's own servers to fetch the file at document_url and send
    it as a ready Document. Falls back to a plain text message (with a link)
    if Telegram can't fetch it (e.g. a private/restricted Drive file)."""
    resp = requests.post(
        SEND_DOCUMENT_API,
        data={
            "chat_id": CHANNEL_USERNAME,
            "document": document_url,
            "caption": caption,
            "parse_mode": "HTML",
        },
        timeout=60,
    )
    if not resp.ok:
        print("Échec de l'envoi du document, envoi du lien à la place :", resp.status_code, resp.text)
        send_telegram_message(f"{caption}\n📎 {document_url}")
        return
    print("Document envoyé.")


def main():
    news = fetch_news()
    if not news:
        print("Aucune actualité trouvée sur la page (structure du site peut-être changée).")
        return

    last_seen_urls = set(load_last_seen())
    current_urls = [item["url"] for item in news]

    # New items = present now but not in last_seen
    new_items = [item for item in news if item["url"] not in last_seen_urls]

    if not last_seen_urls:
        # First run ever: don't spam the channel with the whole history,
        # just record current state as the baseline.
        print(f"Premier lancement : {len(news)} actualités enregistrées comme référence.")
        save_last_seen(current_urls)
        return

    if not new_items:
        print("Aucune nouvelle actualité.")
        return

    # Send oldest-of-the-new first so the channel reads chronologically
    for item in reversed(new_items):
        caption = f"📢 <b>{item['title']}</b>\n{item['url']}"
        attachment_url = find_attachment(item["url"])

        if attachment_url:
            print("Pièce jointe trouvée pour :", item["title"], "->", attachment_url)
            send_telegram_document(attachment_url, caption)
        else:
            send_telegram_message(caption)

        print("Envoyé :", item["title"])

    save_last_seen(current_urls)


if __name__ == "__main__":
    main()
