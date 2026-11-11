"""Threshold calibration shared by the agent and the non-unlocking preview."""
import numpy as np

MIN_FACE_THRESHOLD = 0.50


def calibrate_threshold(known):
    """Anchor calibration only. Never lower it using adaptive samples."""
    if len(known) < 2:
        return MIN_FACE_THRESHOLD
    normalized = known / np.maximum(np.linalg.norm(known, axis=1, keepdims=True), 1e-9)
    pair_scores = (normalized @ normalized.T)[np.triu_indices(len(known), 1)]
    return float(np.clip(np.percentile(pair_scores, 20), MIN_FACE_THRESHOLD, 0.75))
