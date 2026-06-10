import re
import urllib.parse
import requests

def search_duckduckgo(query: str, max_results: int = 5) -> list[dict]:
    """
    Searches DuckDuckGo's free HTML endpoint and returns a list of dictionaries
    with 'title', 'url', and 'snippet'.
    """
    url = "https://html.duckduckgo.com/html/"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    }
    data = {"q": query}
    
    try:
        response = requests.post(url, headers=headers, data=data, timeout=10)
        response.raise_for_status()
        html = response.text
    except Exception as e:
        print(f"DuckDuckGo search error: {e}")
        return []

    results = []
    # Find all result blocks
    blocks = re.findall(r'(?s)<div class="result__body">(.*?)</div>\s*<!-- \.result__body -->', html)
    
    for block in blocks:
        if len(results) >= max_results:
            break
            
        # Extract URL
        # The actual URL is usually in a redirect like href="//duckduckgo.com/l/?uddg=..."
        url_match = re.search(r'href="[^"]*uddg=([^"&]+)[^"]*"', block)
        if url_match:
            result_url = urllib.parse.unquote(url_match.group(1))
        else:
            # Fallback
            url_match2 = re.search(r'class="result__url" href="([^"]+)"', block)
            if url_match2:
                result_url = urllib.parse.unquote(url_match2.group(1))
            else:
                continue

        # Extract Title
        title_match = re.search(r'<h2 class="result__title">.*?<a[^>]*>(.*?)</a>', block, re.IGNORECASE | re.DOTALL)
        if title_match:
            title = re.sub(r'<[^>]+>', '', title_match.group(1)).strip()
        else:
            title = "No Title"

        # Extract Snippet
        snippet_match = re.search(r'<a class="result__snippet[^>]*>(.*?)</a>', block, re.IGNORECASE | re.DOTALL)
        if snippet_match:
            snippet = re.sub(r'<[^>]+>', '', snippet_match.group(1)).strip()
        else:
            snippet = "No snippet"
            
        results.append({
            "title": title,
            "url": result_url,
            "snippet": snippet
        })
        
    return results

if __name__ == "__main__":
    import json
    res = search_duckduckgo("What is python?")
    print(json.dumps(res, indent=2))
