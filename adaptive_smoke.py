"""Checks that learned face samples stay tied to the original enrollment."""

import numpy as np

from unlock_agent import ADAPTIVE_MAX, add_adaptive_sample


def unit(v):
    return (v / np.linalg.norm(v)).astype(np.float32)


def check_rules():
    rng = np.random.default_rng(7)
    base = unit(rng.normal(size=128))
    anchors = np.vstack([unit(base + rng.normal(scale=0.02, size=128)) for _ in range(5)])
    empty = np.empty((0, 128), np.float32)

    drifted = unit(base + rng.normal(scale=0.07, size=128))
    learned = add_adaptive_sample(anchors, empty, drifted)
    assert learned is not None and len(learned) == 1

    assert add_adaptive_sample(anchors, learned, drifted) is None, "duplicate kept"

    stranger = unit(rng.normal(size=128))
    assert add_adaptive_sample(anchors, learned, stranger) is None, "stranger learned"

    full = np.vstack([unit(base + rng.normal(scale=0.07, size=128)) for _ in range(ADAPTIVE_MAX)])
    newest = unit(base + rng.normal(scale=0.07, size=128))
    rolled = add_adaptive_sample(anchors, full, newest)
    assert len(rolled) == ADAPTIVE_MAX and np.allclose(rolled[-1], newest)
    assert np.allclose(rolled[0], full[1]), "oldest sample not dropped"


def check_tampered_file(tmp):
    rng = np.random.default_rng(11)
    base = unit(rng.normal(size=128))
    anchors = np.vstack([unit(base + rng.normal(scale=0.02, size=128)) for _ in range(5)])
    genuine = unit(base + rng.normal(scale=0.07, size=128))
    stranger = unit(rng.normal(size=128))
    unlock_agent.ADAPTIVE_DB = tmp / "adaptive.npy"
    np.save(unlock_agent.ADAPTIVE_DB, np.vstack([genuine, stranger]))
    loaded = unlock_agent.load_adaptive(anchors)
    assert len(loaded) == 1 and np.allclose(loaded[0], genuine), "injected stranger loaded"


if __name__ == "__main__":
    import tempfile
    from pathlib import Path

    import unlock_agent

    unlock_agent.fl.log = lambda msg: None
    check_rules()
    with tempfile.TemporaryDirectory() as tmp:
        check_tampered_file(Path(tmp))
    print("adaptive smoke passed")
