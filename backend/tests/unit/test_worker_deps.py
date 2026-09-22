from app.services.worker import (
    WorkerDeps,
    ensure_production_deps,
    get_deps,
    make_production_deps,
    set_deps,
)


def test_worker_deps_default_skip_audio_true_for_tests():
    assert WorkerDeps().skip_audio is True


def test_make_production_deps_skip_audio_false():
    deps = make_production_deps()
    assert deps.skip_audio is False


def test_ensure_production_deps_does_not_clobber_injected_fakes():
    fake = WorkerDeps(skip_audio=True)
    set_deps(fake)
    try:
        ensure_production_deps()
        assert get_deps() is fake
        assert get_deps().skip_audio is True
    finally:
        set_deps(None)
    ensure_production_deps()
    try:
        assert get_deps().skip_audio is False
    finally:
        set_deps(None)
