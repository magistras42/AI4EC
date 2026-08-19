"""LM Studio embeddings and cosine-similarity ranking."""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
from openai import OpenAI

if TYPE_CHECKING:
    from .config import AgentConfig


class EmbeddingClient:
    def __init__(self, config: AgentConfig):
        self.config = config
        self._client = OpenAI(
            base_url=config.lm_studio_base_url,
            api_key="lm-studio",
            timeout=config.lm_studio_timeout,
        )
        self._model = config.embed_model

    def _resolve_model(self) -> str:
        if self._model:
            return self._model
        models = self._client.models.list()
        for model in models.data:
            mid = model.id.lower()
            if "embed" in mid:
                self._model = model.id
                return model.id
        if models.data:
            self._model = models.data[0].id
            return models.data[0].id
        raise RuntimeError("No embedding model available in LM Studio")

    def embed(self, text: str) -> np.ndarray:
        vectors = self.embed_batch([text])
        return vectors[0]

    def embed_batch(self, texts: list[str]) -> list[np.ndarray]:
        if not texts:
            return []
        model = self._resolve_model()
        vectors: list[np.ndarray] = []
        batch_size = self.config.embed_batch_size

        for start in range(0, len(texts), batch_size):
            chunk = [t.replace("\n", " ") for t in texts[start : start + batch_size]]
            response = self._client.embeddings.create(
                input=chunk,
                model=model,
                encoding_format="float",
            )
            ordered = sorted(response.data, key=lambda item: item.index)
            for item in ordered:
                vec = np.asarray(item.embedding, dtype=np.float64)
                if np.allclose(vec, 0.0):
                    response_b64 = self._client.embeddings.create(
                        input=[chunk[item.index]],
                        model=model,
                        encoding_format="base64",
                    )
                    vec = np.asarray(response_b64.data[0].embedding, dtype=np.float64)
                vectors.append(_normalize(vec))

        return vectors

    def build_index(
        self, premises: dict[str, str]
    ) -> dict[str, np.ndarray]:
        names = list(premises.keys())
        texts = [premises[name] for name in names]
        vectors = self.embed_batch(texts)
        return {name: vec for name, vec in zip(names, vectors)}


def rank_by_cosine(
    premise_index: dict[str, np.ndarray],
    goal_vec: np.ndarray,
    k: int,
) -> list[tuple[str, float]]:
    if not premise_index:
        return []
    goal = _normalize(goal_vec)
    scored: list[tuple[str, float]] = []
    for name, vec in premise_index.items():
        score = float(np.dot(goal, _normalize(vec)))
        scored.append((name, score))
    scored.sort(key=lambda item: item[1], reverse=True)
    return scored[:k]


def top_premises(
    premises: dict[str, str],
    ranked: list[tuple[str, float]],
) -> dict[str, str]:
    return {name: premises[name] for name, _ in ranked if name in premises}


def _normalize(vec: np.ndarray) -> np.ndarray:
    norm = np.linalg.norm(vec)
    if norm == 0:
        return vec
    return vec / norm
