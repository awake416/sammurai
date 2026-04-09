"""
Module for generating and managing vector embeddings for document chunks.
This module provides interfaces for interacting with embedding models and
storing/retrieving vector representations of text data.
"""

from typing import List, Protocol, runtime_checkable
import numpy as np

class EmbeddingError(Exception):
    """Raised when an error occurs during the embedding generation process."""
    pass

@runtime_checkable
class EmbeddingModel(Protocol):
    """
    Interface for embedding models used to transform text into numerical vectors.
    Implementations should handle specific model architectures (e.g., BERT, OpenAI).
    """

    def embed_text(self, text: str) -> List[float]:
        """
        Generates an embedding vector for a single string of text.

        Args:
            text (str): The input text to be embedded.

        Returns:
            List[float]: A list of floats representing the text in vector space.
                         Dimensionality depends on the specific model implementation.

        Raises:
            ValueError: If the input text is empty or whitespace.
            EmbeddingError: If the underlying model fails to process the request.
        """
        ...

    def embed_chunks(self, chunks: List[str]) -> List[List[float]]:
        """
        Generates embedding vectors for a batch of text chunks.

        Args:
            chunks (List[str]): A list of text strings to be embedded.

        Returns:
            List[List[float]]: A list of embedding vectors, one for each input chunk.

        Raises:
            ValueError: If the chunks list is empty.
            EmbeddingError: If the batch processing fails.
        """
        ...

class MockEmbeddingModel:
    """
    A mock implementation of the EmbeddingModel for testing and development.
    Generates deterministic pseudo-random vectors.
    """

    def __init__(self, dimensions: int = 384):
        """
        Initializes the mock model with a specific dimensionality.

        Args:
            dimensions (int): The size of the output vector. Defaults to 384 (common for small models).
        """
        self.dimensions = dimensions

    def embed_text(self, text: str) -> List[float]:
        """
        Generates a mock embedding for a single string.

        Args:
            text (str): The input text.

        Returns:
            List[float]: A normalized vector of length `self.dimensions`.

        Raises:
            ValueError: If text is empty.
        """
        if not text or not text.strip():
            raise ValueError("Input text cannot be empty")
        
        # Use hash of text to create a deterministic mock vector
        seed = hash(text) % (2**32)
        rng = np.random.default_rng(seed)
        vector = rng.standard_normal(self.dimensions)
        
        # Normalization: L2 norm ensures vectors sit on a unit hypersphere,
        # which is standard practice for cosine similarity comparisons.
        norm = np.linalg.norm(vector)
        normalized_vector = (vector / norm).tolist()
        
        return normalized_vector

    def embed_chunks(self, chunks: List[str]) -> List[List[float]]:
        """
        Generates mock embeddings for a batch of chunks.

        Args:
            chunks (List[str]): List of text chunks.

        Returns:
            List[List[float]]: List of normalized vectors.

        Raises:
            ValueError: If chunks list is empty.
        """
        if not chunks:
            raise ValueError("Chunks list cannot be empty")
        
        # Batching strategy: Process sequentially for the mock implementation.
        # In production models, this would typically be a vectorized GPU operation.
        return [self.embed_text(chunk) for chunk in chunks]

class EmbeddingStore:
    """
    Manages the persistence and retrieval of embeddings.
    Acts as a wrapper around vector databases or local caches.
    """

    def __init__(self, model: EmbeddingModel):
        """
        Initializes the store with a specific embedding model.

        Args:
            model (EmbeddingModel): The model used to generate embeddings for new content.
        """
        self.model = model
        self._storage = {}

    def add_document(self, doc_id: str, chunks: List[str]) -> None:
        """
        Embeds and stores a document's chunks.

        Args:
            doc_id (str): Unique identifier for the document.
            chunks (List[str]): The text segments to be indexed.

        Raises:
            EmbeddingError: If the model fails to generate embeddings.
            ValueError: If doc_id already exists or chunks are invalid.
        """
        if doc_id in self._storage:
            raise ValueError(f"Document {doc_id} already exists in store")
        
        embeddings = self.model.embed_chunks(chunks)
        self._storage[doc_id] = list(zip(chunks, embeddings))

    def get_embeddings(self, doc_id: str) -> List[List[float]]:
        """
        Retrieves the stored vectors for a specific document.

        Args:
            doc_id (str): The identifier of the document.

        Returns:
            List[List[float]]: The list of embedding vectors associated with the document.

        Raises:
            KeyError: If the doc_id is not found in the store.
        """
        if doc_id not in self._storage:
            raise KeyError(f"Document {doc_id} not found")
        
        return [item[1] for item in self._storage[doc_id]]