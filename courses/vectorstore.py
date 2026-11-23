import os
import pickle

import faiss
import numpy as np
from sentence_transformers import SentenceTransformer


class VectorStore:
    def __init__(self, persist_dir, embedding_model="all-MiniLM-L6-v2"):
        self.persist_dir = persist_dir
        os.makedirs(self.persist_dir, exist_ok=True)
        self.faiss_path = os.path.join(self.persist_dir, "faiss.index")
        self.meta_path = os.path.join(self.persist_dir, "metadata.pkl")
        self.embedder = _get_embedder(embedding_model)

    def build(self, texts, metadatas):
        embeddings = self.embedder.encode(
            texts,
            convert_to_numpy=True,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        embeddings = np.asarray(embeddings, dtype="float32")
        index = faiss.IndexFlatIP(embeddings.shape[1])
        index.add(embeddings)
        faiss.write_index(index, self.faiss_path)
        with open(self.meta_path, "wb") as handle:
            pickle.dump({"texts": texts, "metadatas": metadatas}, handle)

    def search(self, query, k=5):
        if not os.path.exists(self.faiss_path) or not os.path.exists(self.meta_path):
            return []

        index = faiss.read_index(self.faiss_path)
        with open(self.meta_path, "rb") as handle:
            payload = pickle.load(handle)

        query_vec = self.embedder.encode(
            [query],
            convert_to_numpy=True,
            normalize_embeddings=True,
            show_progress_bar=False,
        ).astype("float32")
        scores, ids = index.search(query_vec, min(k, index.ntotal))
        results = []
        for score, idx in zip(scores[0], ids[0]):
            if idx < 0:
                continue
            results.append(
                {
                    "text": payload["texts"][idx],
                    "metadata": payload["metadatas"][idx],
                    "score": float(score),
                }
            )
        return results


_EMBEDDER = None
_EMBEDDER_NAME = None


def _get_embedder(model_name):
    global _EMBEDDER, _EMBEDDER_NAME
    if _EMBEDDER is None or _EMBEDDER_NAME != model_name:
        _EMBEDDER = SentenceTransformer(model_name)
        _EMBEDDER_NAME = model_name
    return _EMBEDDER
