import numpy as np
from sklearn.metrics.pairwise import cosine_similarity

def is_semantic_duplicate(
    new_vec: np.ndarray,
    existing_vecs: list[np.ndarray],
    threshold: float = 0.88,
) -> bool:
    if not existing_vecs:
        return False

    sims = cosine_similarity(
        [new_vec],
        existing_vecs,
    )[0]

    return max(sims) >= threshold
