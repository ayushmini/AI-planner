import re

def summarize_text(query: str, text: str, chunk_size: int = 500, top_k: int = 3) -> list[str]:
    """
    Splits text into chunks, scores each chunk by keyword overlap with the query
    (ignoring common stop words), and returns the top `top_k` chunks.
    """
    if not text:
        return []
        
    # Simple stop words list
    stop_words = {"what", "is", "the", "a", "an", "how", "to", "in", "of", "and", "or", "for", "on", "with", "as", "by", "at", "it", "this", "that", "are"}
    
    # Extract query keywords
    query_words = re.findall(r'\b\w+\b', query.lower())
    keywords = set(w for w in query_words if w not in stop_words and len(w) > 2)
    
    # Split text into chunks
    words = text.split()
    chunks = []
    
    # Convert word count to approx chunk size (assuming avg 5 chars per word)
    words_per_chunk = max(10, chunk_size // 5)
    
    for i in range(0, len(words), words_per_chunk):
        chunk = " ".join(words[i:i + words_per_chunk])
        chunks.append(chunk)
        
    scored_chunks = []
    for chunk in chunks:
        chunk_lower = chunk.lower()
        score = sum(1 for kw in keywords if kw in chunk_lower)
        scored_chunks.append((score, chunk))
        
    # Sort by score descending
    scored_chunks.sort(key=lambda x: x[0], reverse=True)
    
    # Return top_k chunks
    return [chunk for score, chunk in scored_chunks[:top_k]]
    
if __name__ == "__main__":
    text = "Python is a high-level, general-purpose programming language. Its design philosophy emphasizes code readability with the use of significant indentation. Python is dynamically typed and garbage-collected."
    res = summarize_text("What is Python programming language?", text, chunk_size=100)
    print(res)
