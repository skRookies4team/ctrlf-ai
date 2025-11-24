# langflow/vectorstore/faiss_store.py
import faiss
import numpy as np

class FAISSStore:
    def __init__(self, dim):
        self.dim = dim
        self.index = faiss.IndexFlatL2(dim)
        self.sentences = []

    def add(self, embeddings, sentences):
        """
        embeddings: List[np.array]
        sentences: List[str]
        """
        embeddings = np.array(embeddings).astype('float32')
        self.index.add(embeddings)
        self.sentences.extend(sentences)

    def search(self, query_emb, k=5):
        query_emb = np.array([query_emb]).astype('float32')
        distances, indices = self.index.search(query_emb, k)
        results = [(self.sentences[i], distances[0][idx]) for idx, i in enumerate(indices[0])]
        return results
