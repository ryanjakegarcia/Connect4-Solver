"""ML-model-based move selector.

Drop-in alternative to SolverClient with the same best_move(sequence) -> int
interface. Loads a scikit-learn model trained by CPSC483/scripts/train_connect4_baseline.py.

Feature contract (51-dim):
  42  board cells (side-to-move perspective: +1=self, -1=opp, 0=empty)
   7  column heights
   1  move count
   1  to_move (+1=P1, -1=P2)

best_move() always returns a legal column (legal mask applied before selection).
"""

from __future__ import annotations

import pickle
import random
from pathlib import Path

import numpy as np

_ROWS = 6
_COLS = 7
_LOG_EPS = 1e-8
_EASY_TEMPERATURE = 3.0


def _featurize(sequence: str) -> tuple[np.ndarray, np.ndarray]:
    board = np.zeros((_ROWS, _COLS), dtype=np.int8)
    heights = np.zeros(_COLS, dtype=np.int8)
    player = 1
    for ch in sequence:
        col = int(ch) - 1
        board[heights[col], col] = player
        heights[col] += 1
        player = 2 if player == 1 else 1

    perspective = 1 if player == 1 else -1
    x = np.concatenate([
        (board * perspective).reshape(-1).astype(np.float32),
        heights.astype(np.float32),
        np.array([len(sequence)], dtype=np.float32),
        np.array([float(perspective)], dtype=np.float32),
    ])
    legal = heights < _ROWS
    return x, legal


def _softmax(v: np.ndarray) -> np.ndarray:
    e = np.exp(v - v.max())
    return e / e.sum()


class MLPolicyClient:
    """Sklearn-model move selector compatible with SolverClient.best_move()."""

    DIFFICULTIES = {"hard", "medium", "easy"}

    def __init__(self, model_path: str | Path, difficulty: str = "hard") -> None:
        if difficulty not in self.DIFFICULTIES:
            raise ValueError(f"--ml-difficulty must be one of {self.DIFFICULTIES}")

        self.difficulty = difficulty
        path = Path(model_path)
        if not path.exists():
            raise FileNotFoundError(f"ML model not found: {path}")

        try:
            import joblib
            self.model = joblib.load(path)
        except Exception:
            with open(path, "rb") as f:
                self.model = pickle.load(f)

        self._classes = np.asarray(self.model.classes_)  # 1-based [1..7]
        self._has_proba = hasattr(self.model, "predict_proba")
        if not self._has_proba and not hasattr(self.model, "decision_function"):
            raise ValueError("Model must expose predict_proba or decision_function")

    def _raw_scores(self, x: np.ndarray) -> np.ndarray:
        if self._has_proba:
            return self.model.predict_proba(x)[0]
        s = self.model.decision_function(x)
        return s[0] if s.ndim == 2 else s

    def best_move(self, sequence: str) -> int:
        """Return 0-based column index for the best legal move."""
        x, legal = _featurize(sequence)
        raw = self._raw_scores(x.reshape(1, -1))

        col_scores = np.full(_COLS, -np.inf)
        for i, cls in enumerate(self._classes):
            col = int(cls) - 1
            if 0 <= col < _COLS:
                col_scores[col] = raw[i]
        col_scores[~legal] = -np.inf

        legal_cols = np.flatnonzero(legal)
        if len(legal_cols) == 0:
            raise RuntimeError(f"No legal moves for sequence {sequence!r}")

        if self.difficulty == "hard":
            return int(np.argmax(col_scores))

        legal_scores = col_scores[legal_cols]
        if self.difficulty == "medium":
            probs = _softmax(legal_scores)
        else:  # easy
            log_p = np.log(np.maximum(_softmax(legal_scores), _LOG_EPS))
            probs = _softmax(log_p / _EASY_TEMPERATURE)

        return int(random.choices(legal_cols.tolist(), weights=probs.tolist(), k=1)[0])

    def close(self) -> None:
        pass


class NeuralPolicyClient:
    """CNN (+ optional MCTS) move selector. Uses Connect4Net from 483-Connect4-ML.

    Requires torch. model_path = .pth checkpoint from train_network.py.
    src_path = path to 483-Connect4-ML/src/ containing network.py and mcts.py.
    simulations=0 → pure network greedy; simulations>0 → AlphaZero MCTS.
    """

    def __init__(
        self,
        model_path: str | Path,
        src_path: str | Path,
        filters: int = 64,
        n_residuals: int = 6,
        simulations: int = 200,
        device: str | None = None,
    ) -> None:
        import sys
        sys.path.insert(0, str(Path(src_path).resolve()))

        from network import Connect4Net, NetConfig, MCTSClient, Connect4NetClient

        self._path = str(model_path)
        cfg = NetConfig(filters=filters, n_residuals=n_residuals)

        if simulations > 0:
            self._client = MCTSClient(self._path, net_config=cfg, simulations=simulations, device=device)
        else:
            self._client = Connect4NetClient(self._path, net_config=cfg, difficulty="hard", device=device)

        self.simulations = simulations

    def best_move(self, sequence: str) -> int:
        return self._client.best_move(sequence)

    def close(self) -> None:
        self._client.close()
