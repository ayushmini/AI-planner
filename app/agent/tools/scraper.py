import re
import requests

def scrape_url(url: str, max_chars: int = 3000) -> str:
    """
    Fetches a URL using requests, strips all HTML tags, and returns
    cleaned text capped at `max_chars` characters.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36"
    }
    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        html = response.text
        
        # Simple tag removal
        # First remove script and style tags completely
        text = re.sub(r'(?is)<(script|style).*?>.*?</\1>', '', html)
        # Then remove all other tags
        text = re.sub(r'<[^>]+>', ' ', text)
        # Decode some basic HTML entities if possible (since we can't use html module)
        text = text.replace('&nbsp;', ' ').replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
        # Remove extra whitespace
        text = re.sub(r'\s+', ' ', text).strip()
        
        if len(text) > max_chars:
            text = text[:max_chars]
            # Try to cut at the last space
            last_space = text.rfind(' ')
            if last_space > 0:
                text = text[:last_space] + '...'
                
        return text
    except Exception as e:
        print(f"Scraper error for {url}: {e}")
        return ""

if __name__ == "__main__":
    content = scrape_url("https://en.wikipedia.org/wiki/Python_(programming_language)")
    print(content[:500])
