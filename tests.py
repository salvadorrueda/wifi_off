"""Tests for the wifi_off Flask application."""

import threading
import time
from unittest.mock import MagicMock, call, patch

import pytest

import app as app_module
from app import app, wifi_state, state_lock


@pytest.fixture(autouse=True)
def reset_wifi_state():
    """Reset global wifi_state before each test."""
    with state_lock:
        if wifi_state.get("timer"):
            wifi_state["timer"].cancel()
        wifi_state["disabled"] = False
        wifi_state["re_enable_at"] = None
        wifi_state["timer"] = None
        wifi_state["last_error"] = None
    yield


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


# ---------------------------------------------------------------------------
# Helper: mock RouterOsApiPool so no real network connection is made
# ---------------------------------------------------------------------------

def make_mock_pool(interfaces=None):
    """Return a MagicMock that simulates routeros_api.RouterOsApiPool."""
    if interfaces is None:
        interfaces = [
            {".id": "*1", "name": "wlan1", "disabled": "false"},
            {".id": "*2", "name": "wlan2", "disabled": "false"},
        ]
    mock_wireless = MagicMock()
    mock_wireless.get.return_value = interfaces

    mock_api = MagicMock()
    mock_api.get_resource.return_value = mock_wireless

    mock_pool = MagicMock()
    mock_pool.get_api.return_value = mock_api

    return mock_pool, mock_wireless


# ---------------------------------------------------------------------------
# Route: GET /
# ---------------------------------------------------------------------------

class TestIndex:
    def test_index_renders(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert b"WiFi Off" in resp.data

    def test_index_shows_env_notice_when_host_set(self, client, monkeypatch):
        monkeypatch.setenv("MIKROTIK_HOST", "10.0.0.1")
        monkeypatch.setenv("MIKROTIK_USER", "admin")
        resp = client.get("/")
        assert resp.status_code == 200
        assert b"10.0.0.1" in resp.data

    def test_index_shows_credential_form_without_env(self, client, monkeypatch):
        monkeypatch.delenv("MIKROTIK_HOST", raising=False)
        monkeypatch.delenv("MIKROTIK_USER", raising=False)
        resp = client.get("/")
        assert resp.status_code == 200
        assert b'name="host"' in resp.data


# ---------------------------------------------------------------------------
# Route: GET /status
# ---------------------------------------------------------------------------

class TestStatus:
    def test_status_wifi_enabled(self, client):
        resp = client.get("/status")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["disabled"] is False
        assert data["remaining_seconds"] is None

    def test_status_wifi_disabled(self, client):
        from datetime import datetime, timedelta, timezone
        re_enable = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()
        with state_lock:
            wifi_state["disabled"] = True
            wifi_state["re_enable_at"] = re_enable

        resp = client.get("/status")
        data = resp.get_json()
        assert data["disabled"] is True
        assert data["remaining_seconds"] > 0

    def test_status_exposes_last_error(self, client):
        with state_lock:
            wifi_state["last_error"] = "Connection refused"
        resp = client.get("/status")
        data = resp.get_json()
        assert data["last_error"] == "Connection refused"


# ---------------------------------------------------------------------------
# Route: POST /wifi/off
# ---------------------------------------------------------------------------

class TestWifiOff:
    def test_wifi_off_success(self, client, monkeypatch):
        mock_pool, mock_wireless = make_mock_pool()
        monkeypatch.setenv("MIKROTIK_HOST", "192.168.1.1")
        monkeypatch.setenv("MIKROTIK_USER", "admin")
        monkeypatch.setenv("MIKROTIK_PASSWORD", "")

        with patch("app.routeros_api.RouterOsApiPool", return_value=mock_pool):
            resp = client.post("/wifi/off", data={"minutes": "10"})

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["re_enable_at"] is not None

        with state_lock:
            assert wifi_state["disabled"] is True
            assert wifi_state["timer"] is not None
            wifi_state["timer"].cancel()  # cleanup

    def test_wifi_off_disables_all_interfaces(self, client, monkeypatch):
        mock_pool, mock_wireless = make_mock_pool()
        monkeypatch.setenv("MIKROTIK_HOST", "192.168.1.1")
        monkeypatch.setenv("MIKROTIK_USER", "admin")
        monkeypatch.setenv("MIKROTIK_PASSWORD", "")

        with patch("app.routeros_api.RouterOsApiPool", return_value=mock_pool):
            client.post("/wifi/off", data={"minutes": "5"})

        disable_calls = [c for c in mock_wireless.call.call_args_list
                         if c.args[0] == "disable"]
        assert len(disable_calls) == 2  # two interfaces
        with state_lock:
            if wifi_state.get("timer"):
                wifi_state["timer"].cancel()

    def test_wifi_off_invalid_minutes(self, client, monkeypatch):
        monkeypatch.setenv("MIKROTIK_HOST", "192.168.1.1")
        monkeypatch.setenv("MIKROTIK_USER", "admin")
        resp = client.post("/wifi/off", data={"minutes": "0"})
        assert resp.status_code == 400
        assert resp.get_json()["ok"] is False

    def test_wifi_off_non_numeric_minutes(self, client, monkeypatch):
        monkeypatch.setenv("MIKROTIK_HOST", "192.168.1.1")
        monkeypatch.setenv("MIKROTIK_USER", "admin")
        resp = client.post("/wifi/off", data={"minutes": "abc"})
        assert resp.status_code == 400

    def test_wifi_off_already_disabled(self, client, monkeypatch):
        monkeypatch.setenv("MIKROTIK_HOST", "192.168.1.1")
        monkeypatch.setenv("MIKROTIK_USER", "admin")
        with state_lock:
            wifi_state["disabled"] = True

        resp = client.post("/wifi/off", data={"minutes": "10"})
        assert resp.status_code == 400
        assert resp.get_json()["ok"] is False

    def test_wifi_off_missing_host(self, client, monkeypatch):
        monkeypatch.delenv("MIKROTIK_HOST", raising=False)
        monkeypatch.delenv("MIKROTIK_USER", raising=False)
        resp = client.post("/wifi/off", data={"minutes": "10"})
        assert resp.status_code == 400
        assert "connexió" in resp.get_json()["error"]

    def test_wifi_off_connection_error(self, client, monkeypatch):
        monkeypatch.setenv("MIKROTIK_HOST", "192.168.1.1")
        monkeypatch.setenv("MIKROTIK_USER", "admin")
        monkeypatch.setenv("MIKROTIK_PASSWORD", "")

        with patch("app.routeros_api.RouterOsApiPool", side_effect=Exception("timeout")):
            resp = client.post("/wifi/off", data={"minutes": "5"})

        assert resp.status_code == 500
        assert "router" in resp.get_json()["error"]

    def test_wifi_off_uses_form_credentials_when_no_env(self, client, monkeypatch):
        monkeypatch.delenv("MIKROTIK_HOST", raising=False)
        monkeypatch.delenv("MIKROTIK_USER", raising=False)
        monkeypatch.delenv("MIKROTIK_PASSWORD", raising=False)

        mock_pool, _ = make_mock_pool()
        with patch("app.routeros_api.RouterOsApiPool", return_value=mock_pool) as mock_cls:
            client.post("/wifi/off", data={
                "minutes": "5",
                "host": "10.0.0.1",
                "username": "admin",
                "password": "secret",
            })

        mock_cls.assert_called_once()
        call_kwargs = mock_cls.call_args
        assert call_kwargs.args[0] == "10.0.0.1"
        assert call_kwargs.kwargs["username"] == "admin"
        assert call_kwargs.kwargs["password"] == "secret"
        with state_lock:
            if wifi_state.get("timer"):
                wifi_state["timer"].cancel()


# ---------------------------------------------------------------------------
# Route: POST /wifi/on
# ---------------------------------------------------------------------------

class TestWifiOn:
    def test_wifi_on_success(self, client, monkeypatch):
        monkeypatch.setenv("MIKROTIK_HOST", "192.168.1.1")
        monkeypatch.setenv("MIKROTIK_USER", "admin")
        monkeypatch.setenv("MIKROTIK_PASSWORD", "")

        mock_pool, mock_wireless = make_mock_pool()
        timer = threading.Timer(3600, lambda: None)
        timer.start()

        with state_lock:
            wifi_state["disabled"] = True
            wifi_state["timer"] = timer
            wifi_state["re_enable_at"] = "2099-01-01T00:00:00"

        with patch("app.routeros_api.RouterOsApiPool", return_value=mock_pool):
            resp = client.post("/wifi/on", data={})

        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True
        with state_lock:
            assert wifi_state["disabled"] is False
            assert wifi_state["timer"] is None

    def test_wifi_on_re_enables_all_interfaces(self, client, monkeypatch):
        monkeypatch.setenv("MIKROTIK_HOST", "192.168.1.1")
        monkeypatch.setenv("MIKROTIK_USER", "admin")
        monkeypatch.setenv("MIKROTIK_PASSWORD", "")

        mock_pool, mock_wireless = make_mock_pool()
        with state_lock:
            wifi_state["disabled"] = True

        with patch("app.routeros_api.RouterOsApiPool", return_value=mock_pool):
            client.post("/wifi/on", data={})

        enable_calls = [c for c in mock_wireless.call.call_args_list
                        if c.args[0] == "enable"]
        assert len(enable_calls) == 2

    def test_wifi_on_already_enabled(self, client, monkeypatch):
        monkeypatch.setenv("MIKROTIK_HOST", "192.168.1.1")
        monkeypatch.setenv("MIKROTIK_USER", "admin")
        resp = client.post("/wifi/on", data={})
        assert resp.status_code == 400

    def test_wifi_on_missing_host(self, client, monkeypatch):
        monkeypatch.delenv("MIKROTIK_HOST", raising=False)
        monkeypatch.delenv("MIKROTIK_USER", raising=False)
        with state_lock:
            wifi_state["disabled"] = True
        resp = client.post("/wifi/on", data={})
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Internal helper: _get_connection_params
# ---------------------------------------------------------------------------

class TestGetConnectionParams:
    def test_env_vars_take_precedence(self, monkeypatch):
        monkeypatch.setenv("MIKROTIK_HOST", "10.1.1.1")
        monkeypatch.setenv("MIKROTIK_USER", "root")
        monkeypatch.setenv("MIKROTIK_PASSWORD", "pass123")
        host, user, pw = app_module._get_connection_params(
            {"host": "should-be-ignored", "username": "ignored", "password": "ignored"}
        )
        assert host == "10.1.1.1"
        assert user == "root"
        assert pw == "pass123"

    def test_falls_back_to_form_data(self, monkeypatch):
        monkeypatch.delenv("MIKROTIK_HOST", raising=False)
        monkeypatch.delenv("MIKROTIK_USER", raising=False)
        monkeypatch.delenv("MIKROTIK_PASSWORD", raising=False)
        host, user, pw = app_module._get_connection_params(
            {"host": "192.168.0.1", "username": "admin", "password": "mypass"}
        )
        assert host == "192.168.0.1"
        assert user == "admin"
        assert pw == "mypass"
