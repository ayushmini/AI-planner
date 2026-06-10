import re
from app.services import llm_client
from app.agent.tools.search import search_duckduckgo
from app.agent.tools.scraper import scrape_url
from app.agent.tools.summarizer import summarize_text

def should_search(query: str) -> bool:
    """
    Decides if a web search is needed based on simple keyword matching.
    """
    trigger_words = [r"\bwhat is\b", r"\bhow to\b", r"\blatest\b", r"\btips\b", r"\bexplain\b", r"\btoday\b", r"\brecent\b"]
    query_lower = query.lower()
    for trigger in trigger_words:
        if re.search(trigger, query_lower):
            return True
    return False

def run_agent(user_query: str, session_messages: str, system_prompt: str) -> str:
    """
    Decides if search is needed. If so, runs the full search -> scrape -> summarize pipeline,
    injects the knowledge into the system prompt, and calls the LLM.
    """
    knowledge_block = ""
    
    if should_search(user_query):
        print("Web search triggered for:", user_query)
        # 1. Search DuckDuckGo
        results = search_duckduckgo(user_query, max_results=3)
        if results:
            # 2. Pick the first result with a valid URL
            top_result = results[0]
            url = top_result.get("url")
            if url:
                print(f"Scraping URL: {url}")
                # 3. Scrape
                text = scrape_url(url)
                if text:
                    # 4. Summarize
                    chunks = summarize_text(user_query, text)
                    if chunks:
                        knowledge_block = "\n\nWEB KNOWLEDGE CONTEXT:\n"
                        knowledge_block += f"Source: {url}\n"
                        for i, chunk in enumerate(chunks, 1):
                            knowledge_block += f"Chunk {i}: {chunk}\n"
                        knowledge_block += "Use the above web knowledge to help answer the user's query if relevant.\n"

    # Inject knowledge block into the system prompt if we have any
    final_system_prompt = system_prompt + knowledge_block

    # Call the existing LLM pipeline
    messages = [
        {"role": "system", "content": final_system_prompt},
        {"role": "user", "content": session_messages},
    ]
    
    return llm_client.chat_completion(messages, temperature=0.4)
