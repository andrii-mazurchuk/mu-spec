from __future__ import annotations

import json

from mu_spec.lifecycle import Lifecycle
from mu_spec.shipping import ENTRY_TYPE, UNIT_NAME, resolve_logs_url, ship


def _wire(tmp_path, target="mu-logs", base="http://127.0.0.1:9002"):
    (tmp_path / "delivery_policy.json").write_text(
        json.dumps({"logs": target, "owner": "andrey"}), encoding="utf-8"
    )
    (tmp_path / "peers.json").write_text(
        json.dumps(
            [
                {"name": "mu-logs", "base_url": base, "unit_type": "memory"},
                {"name": "other", "base_url": "http://127.0.0.1:9999"},
            ]
        ),
        encoding="utf-8",
    )


def _event(tmp_path):
    log = Lifecycle(tmp_path / "lifecycle.jsonl")
    return log.record("request", "m", lambda: 5.0, refs=["msg-0001"], type="feature")


# -- resolving the peer ------------------------------------------------------


def test_the_logs_peer_is_found_by_role_not_by_name(tmp_path):
    """A hardcoded unit name makes that unit un-renameable. The role is
    resolved from the policy the gateway writes."""
    _wire(tmp_path)
    assert resolve_logs_url(tmp_path) == "http://127.0.0.1:9002"


def test_a_policy_naming_a_peer_that_is_not_registered_resolves_to_nothing(tmp_path):
    _wire(tmp_path, target="mu-nowhere")
    assert resolve_logs_url(tmp_path) is None


def test_no_policy_at_all_resolves_to_nothing(tmp_path):
    """Running without a gateway around it is normal -- every test and the
    walkthrough do exactly that."""
    assert resolve_logs_url(tmp_path) is None


def test_a_malformed_policy_is_an_empty_policy_not_an_error(tmp_path):
    (tmp_path / "delivery_policy.json").write_text("{ not json", encoding="utf-8")
    assert resolve_logs_url(tmp_path) is None


def test_an_empty_policy_is_enforce_nothing(tmp_path):
    (tmp_path / "delivery_policy.json").write_text("{}", encoding="utf-8")
    assert resolve_logs_url(tmp_path) is None


# -- shipping ----------------------------------------------------------------


def test_an_event_ships_in_the_standard_envelope(tmp_path):
    _wire(tmp_path)
    sent = {}

    def sender(url, body):
        sent["url"], sent["body"] = url, body
        return True

    assert ship(tmp_path, _event(tmp_path), sender) is True
    assert sent["url"] == "http://127.0.0.1:9002/entries"
    assert sent["body"]["source_unit"] == UNIT_NAME
    assert sent["body"]["entry_type"] == ENTRY_TYPE
    assert sent["body"]["payload"]["kind"] == "request"


def test_the_id_and_timestamp_are_not_ours_to_set(tmp_path):
    """The receiving unit assigns both. Sending them would be a second
    opinion about when something happened."""
    _wire(tmp_path)
    sent = {}
    ship(tmp_path, _event(tmp_path), lambda u, b: sent.update(b) or True)
    assert "id" not in sent
    assert "timestamp" not in sent


def test_nothing_ships_when_there_is_no_logs_peer(tmp_path):
    called = []
    assert ship(tmp_path, _event(tmp_path), lambda u, b: called.append(u)) is False
    assert called == []


def test_a_refused_entry_type_is_just_a_failed_ship(tmp_path):
    """The type is new and the receiving vocabulary is enforced there. Until
    it is added every attempt is refused, and nothing here cares."""
    _wire(tmp_path)
    assert ship(tmp_path, _event(tmp_path), lambda u, b: False) is False


def test_a_sender_that_raises_is_still_just_a_failed_ship(tmp_path):
    """The outermost degradation boundary. Nothing may escape from here into
    the request handler that triggered the write."""
    _wire(tmp_path)

    def explode(url, body):
        raise ConnectionResetError("peer went away mid-write")

    assert ship(tmp_path, _event(tmp_path), explode) is False


def test_a_failing_sink_never_breaks_the_local_write(tmp_path):
    """The local log is the durable record. Shipping is a copy, and a sink
    that refuses, hangs up or raises must not turn a successful amendment
    into a failed one."""
    from mu_spec.lifecycle import Lifecycle

    def hostile(event):
        raise TimeoutError("logs unit went away")

    log = Lifecycle(tmp_path / "lifecycle.jsonl", sink=hostile)
    event = log.record("request", "m", lambda: 1.0, refs=["msg-0001"])
    assert event is not None
    assert [e.kind for e in log.list()] == ["request"]
