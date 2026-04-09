"""Retrieval module implementing similarity search over stored embeddings to find relevant document chunks for a given query."""

from typing import List, Dict, Any, Optional
from dataclasses import dataclass


class DatabaseError(Exception):
    """Raised when a database operation fails during retrieval."""
    pass


@dataclass
class RetrievalResult:
    """Data class representing a retrieved document chunk and its relevance score."""
    chunk_id: str
    content: str
    score: float
    metadata: Dict[str, Any]


class VectorRetriever:
    """
    Handles similarity search operations against a vector database to find relevant context.
    """

    def __init__(self, vector_store: Any):
        """
        Initializes the retriever with a specific vector store backend.

        Args:
            vector_store: The underlying vector database client or interface.
        """
        self.vector_store = vector_store

    def search(
        self, 
        query: str, 
        top_k: int = 5, 
        threshold: float = 0.0
    ) -> List[RetrievalResult]:
        """
        Performs a similarity search to find the most relevant document chunks.

        Args:
            query (str): The natural language query string to search for.
            top_k (int): The maximum number of relevant chunks to return. Defaults to 5.
            threshold (float): The minimum similarity score required for a result to be included. 
                Defaults to 0.0.

        Returns:
            List[RetrievalResult]: A list of document chunks and their similarity scores, 
                sorted by relevance.

        Raises:
            DatabaseError: If the connection to the vector store fails or the query execution errors.
            ValueError: If the query is empty or parameters are out of valid range.
        """
        if not query.strip():
            raise ValueError("Query string cannot be empty.")

        try:
            # The vector store uses Cosine Similarity (1 - Cosine Distance) to rank results.
            # This metric is robust to variations in document length and focuses on orientation.
            raw_results = self.vector_store.similarity_search_with_score(
                query, 
                k=top_k
            )

            results = []
            for doc, score in raw_results:
                # Filter results based on the provided similarity threshold
                if score >= threshold:
                    results.append(
                        RetrievalResult(
                            chunk_id=doc.metadata.get("id", ""),
                            content=doc.page_content,
                            score=float(score),
                            metadata=doc.metadata
                        )
                    )

            # Re-ranking logic: Currently results are returned based on raw vector similarity.
            # Future iterations may include a Cross-Encoder re-ranker here for higher precision.
            return sorted(results, key=lambda x: x.score, reverse=True)

        except Exception as e:
            raise DatabaseError(f"Failed to retrieve documents: {str(e)}") from e

    def get_relevant_context(self, query: str, context_limit: int = 2000) -> str:
        """
        Retrieves and concatenates document chunks into a single context string for LLM consumption.

        Args:
            query (str): The user query to find context for.
            context_limit (int): The maximum character length of the combined context string. 
                Defaults to 2000.

        Returns:
            str: A concatenated string of relevant document content.

        Raises:
            DatabaseError: If the retrieval process encounters a backend error.
            ValueError: If the query is invalid.
        """
        results = self.search(query, top_k=10)
        context_parts = []
        current_length = 0

        for res in results:
            if current_length + len(res.content) > context_limit:
                break
            context_parts.append(res.content)
            current_length += len(res.content)

        return "\n\n".join(context_parts)