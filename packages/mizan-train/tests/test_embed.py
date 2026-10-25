"""Embedding fine-tuning helpers (the training loop itself runs in `mizan-train embed`)."""

from types import SimpleNamespace

import numpy as np
import pytest
import torch

from mizan_train.embed import freeze_lower_layers, ranking_metrics


def test_ranking_metrics() -> None:
    passages = np.eye(3, dtype=np.float32)
    queries = np.array([[1, 0, 0], [0.9, 0.1, 0], [0, 0, 1]], dtype=np.float32)
    # Query 2's gold is passage 1, ranked second.
    metrics = ranking_metrics(queries, passages, [0, 1, 2])
    assert metrics["R@1"] == pytest.approx(2 / 3)
    assert metrics["R@5"] == 1.0
    assert metrics["MRR@10"] == pytest.approx((1 + 0.5 + 1) / 3)


def test_only_the_top_layers_stay_trainable() -> None:
    layers = torch.nn.ModuleList(torch.nn.Linear(4, 4) for _ in range(6))
    embeddings = torch.nn.Embedding(10, 4)
    transformer = torch.nn.Module()
    transformer.embeddings = embeddings
    transformer.encoder = SimpleNamespace(layer=layers)  # type: ignore[assignment]
    transformer.layers = layers  # registers the layers as submodules for .parameters()
    model = [SimpleNamespace(auto_model=transformer)]

    class Model(list[SimpleNamespace]):
        def parameters(self) -> list[torch.nn.Parameter]:
            return list(transformer.parameters())

    trainable = freeze_lower_layers(Model(model), 2)

    assert trainable == 2 * (4 * 4 + 4)
    assert not embeddings.weight.requires_grad
    assert [layer.weight.requires_grad for layer in layers] == [False] * 4 + [True] * 2
