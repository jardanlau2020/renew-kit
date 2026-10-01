from renewkit.outcome import Outcome, classify_status


def test_only_failed_is_error():
    assert Outcome.FAILED.is_error is True
    for o in (Outcome.RENEWED, Outcome.SKIPPED, Outcome.ALREADY_MAX, Outcome.TRANSIENT):
        assert o.is_error is False


def test_exit_codes():
    assert Outcome.FAILED.exit_code == 1
    assert Outcome.TRANSIENT.exit_code == 0
    assert Outcome.SKIPPED.exit_code == 0


def test_classify_status():
    assert classify_status(200) is Outcome.RENEWED
    assert classify_status(204) is Outcome.RENEWED
    # Cloudflare 522 是上游故障，不能算失败
    assert classify_status(522) is Outcome.TRANSIENT
    assert classify_status(503) is Outcome.TRANSIENT
    assert classify_status(429) is Outcome.TRANSIENT
    assert classify_status(0) is Outcome.TRANSIENT
    assert classify_status(401) is Outcome.FAILED
    assert classify_status(404) is Outcome.FAILED
