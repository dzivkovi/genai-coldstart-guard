"""Unit tests for the Databricks state classifier.

These exercise the classification layer directly. The mock backend short-circuits
to canned responses and never calls classify_state(), so before these tests existed
the classifier had zero coverage and the stopped-detection bug was invisible. See
docs/databricks-endpoint-states.md for the validated state model these encode.
"""

import pytest

from app.databricks_client import classify_state


@pytest.mark.parametrize(
    "state, expected",
    [
        # Warm and scaled-to-zero both report READY; cold start only shows at inference.
        ({"ready": "READY", "config_update": "NOT_UPDATING"}, "ready"),
        # Stopped: NOT_READY with no in-progress update. Old code returned "updating" (the bug).
        ({"ready": "NOT_READY", "config_update": "NOT_UPDATING"}, "stopped"),
        # Deploying / config change in progress.
        ({"ready": "NOT_READY", "config_update": "IN_PROGRESS"}, "updating"),
        ({"ready": "READY", "config_update": "IN_PROGRESS"}, "updating"),
        # Update failed / canceled: not trustworthy, but not "stopped" either.
        ({"ready": "READY", "config_update": "UPDATE_FAILED"}, "updating"),
        ({"ready": "NOT_READY", "config_update": "UPDATE_CANCELED"}, "updating"),
        # Older API field name "update_state" is still honored.
        ({"ready": "READY", "update_state": "NOT_UPDATING"}, "ready"),
        ({"ready": "NOT_READY", "update_state": "IN_PROGRESS"}, "updating"),
        # Unknown / empty shapes stay conservative (never serve blindly).
        ({}, "updating"),
        ({"ready": "SOMETHING_NEW"}, "updating"),
    ],
)
def test_classify_state(state, expected):
    assert classify_state(state) == expected


def test_stopped_is_not_misread_as_updating():
    """Regression guard for the string-match bug: a real stopped state must be 'stopped'."""
    assert classify_state({"ready": "NOT_READY", "config_update": "NOT_UPDATING"}) == "stopped"
