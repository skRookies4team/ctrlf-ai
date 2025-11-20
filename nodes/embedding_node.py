# langflow/nodes/embedding_node.py
import numpy as np

class EmbeddingNode:
    """
    임베딩 생성 모듈 (간단하게 예시용, 실제로는 OpenAI, SentenceTransformers 등 사용)
    """
    def __init__(self):
        pass

    def run(self, text: str):
        # 여기서는 단순 예시: 각 글자 ASCII 합으로 임베딩
        vec = np.array([ord(c) for c in text[:512]])  # 512길이 제한
        # 0 패딩
        if len(vec) < 512:
            vec = np.pad(vec, (0, 512 - len(vec)))
        return vec
