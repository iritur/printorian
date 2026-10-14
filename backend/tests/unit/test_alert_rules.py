"""The alert rules and the code they watch cannot part company (`deploy/observability`).

`promtool test rules` proves what each rule *does* (CI job `alerts`). What it cannot
see is this repository: it is handed the series names the test file invents, so a
rule on a metric the API stopped serving goes on passing its own test for ever
while, on the farm, it evaluates against nothing and never fires. That is the
failure `core.metrics` names over its constants — a renamed series and a green
dashboard reading a series nobody writes — and an alert that cannot fire is the
worse half of it, because silence is what a healthy farm looks like too.

So, as rules over parsed YAML rather than searches of text:

* **every `printorian_*` name a rule reads is one `GET /metrics` serves** —
  `metrics.SERVED`, the tuple the scrape and the doc-drift test already share;
* **every job a rule selects is one the scrape config defines**, because the
  `up{job=...}` fence is what keeps one outage from paging four times;
* **every alert carries a severity the routing knows** and the two annotations a
  person reading the phone needs;
* **every alert has a promtool test in which it fires** — the test file asserting
  silence alone would pass for a rule that can never fire, which is the one
  property worth proving.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from printorian.core import metrics

# `parents[2]` is the backend root; the rules live in `deploy/`, above it.
ROOT = Path(__file__).resolve().parents[3]
OBSERVABILITY = ROOT / "deploy" / "observability"
RULES = OBSERVABILITY / "rules" / "printorian.yml"
RULE_TESTS = OBSERVABILITY / "tests" / "printorian.test.yml"
SCRAPE = OBSERVABILITY / "scrape.yml"

#: The routing contract: the phone, the daily mail, and the dead-man's switch.
SEVERITIES = {"page", "digest", "heartbeat"}

_OUR_SERIES = re.compile(r"\bprintorian_[a-z_]+\b")
_JOB_SELECTOR = re.compile(r'\bjob\s*=\s*"([^"]+)"')


def _load(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _alerts() -> list[dict[str, Any]]:
    return [rule for group in _load(RULES)["groups"] for rule in group["rules"] if "alert" in rule]


def test_every_scanned_file_exists() -> None:
    """A moved file would otherwise turn every check below into a check of nothing."""
    for path in (RULES, RULE_TESTS, SCRAPE):
        assert path.is_file(), path


def test_every_series_a_rule_reads_is_one_the_api_serves() -> None:
    served = set(metrics.SERVED)
    read = {name for rule in _alerts() for name in _OUR_SERIES.findall(rule["expr"])}
    assert read, "no rule reads a printorian_* series — the pattern has stopped matching"
    assert read <= served, f"rules read series /metrics does not serve: {sorted(read - served)}"


def test_every_job_a_rule_selects_is_one_the_scrape_defines() -> None:
    defined = {job["job_name"] for job in _load(SCRAPE)["scrape_configs"]}
    selected = {job for rule in _alerts() for job in _JOB_SELECTOR.findall(rule["expr"])}
    assert selected, "no rule selects a job — the fence against one outage paging four times"
    assert selected <= defined, f"rules select undefined jobs: {sorted(selected - defined)}"


@pytest.mark.parametrize("alert", _alerts(), ids=lambda alert: alert["alert"])
def test_every_alert_is_routed_and_readable(alert: dict[str, Any]) -> None:
    assert alert.get("labels", {}).get("severity") in SEVERITIES
    annotations = alert.get("annotations", {})
    assert annotations.get("summary"), "the line the phone shows"
    assert annotations.get("description"), "what to look at next"


def test_every_alert_has_a_test_in_which_it_fires() -> None:
    fired = {
        case["alertname"]
        for test in _load(RULE_TESTS)["tests"]
        for case in test.get("alert_rule_test", [])
        if case.get("exp_alerts")
    }
    names = {alert["alert"] for alert in _alerts()}
    assert names - fired == set(), f"alerts never shown to fire: {sorted(names - fired)}"


def test_the_promtool_tests_load_these_rules() -> None:
    """The proofs must be of this file, not of a copy beside it."""
    files = [(RULE_TESTS.parent / name).resolve() for name in _load(RULE_TESTS)["rule_files"]]
    assert files == [RULES.resolve()]
