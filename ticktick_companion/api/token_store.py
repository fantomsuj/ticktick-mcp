"""Token storage backends for TickTick OAuth credentials."""

import json
import logging
import os
from pathlib import Path
from typing import Dict, Optional

import requests
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

TOKEN_KEYS = ("TICKTICK_ACCESS_TOKEN", "TICKTICK_REFRESH_TOKEN")


class TokenStoreError(Exception):
    """Raised when a durable token store cannot complete an operation."""


class TokenStore:
    """Small interface for loading and saving TickTick OAuth tokens."""

    def load_tokens(self) -> Dict[str, str]:
        raise NotImplementedError

    def save_tokens(self, tokens: Dict[str, str]) -> None:
        raise NotImplementedError


class EnvFileTokenStore(TokenStore):
    """Store tokens in environment variables and a local .env file."""

    def __init__(self, env_path: str = ".env"):
        self.env_path = Path(env_path)

    def _read_env_file(self) -> Dict[str, str]:
        env_content: Dict[str, str] = {}
        if not self.env_path.exists():
            return env_content
        try:
            with open(self.env_path, "r") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        key, value = line.split("=", 1)
                        env_content[key] = value
        except OSError as e:
            raise TokenStoreError(f"Could not read token env file {self.env_path}: {e}") from e
        return env_content

    def load_tokens(self) -> Dict[str, str]:
        load_dotenv()
        env_content = self._read_env_file()
        return {
            key: env_content.get(key) or os.getenv(key, "")
            for key in TOKEN_KEYS
            if env_content.get(key) or os.getenv(key)
        }

    def save_tokens(self, tokens: Dict[str, str]) -> None:
        env_content = self._read_env_file()
        if tokens.get("access_token"):
            env_content["TICKTICK_ACCESS_TOKEN"] = tokens["access_token"]
        if tokens.get("refresh_token"):
            env_content["TICKTICK_REFRESH_TOKEN"] = tokens["refresh_token"]

        client_id = os.getenv("TICKTICK_CLIENT_ID")
        client_secret = os.getenv("TICKTICK_CLIENT_SECRET")
        if client_id and "TICKTICK_CLIENT_ID" not in env_content:
            env_content["TICKTICK_CLIENT_ID"] = client_id
        if client_secret and "TICKTICK_CLIENT_SECRET" not in env_content:
            env_content["TICKTICK_CLIENT_SECRET"] = client_secret

        try:
            with open(self.env_path, "w") as f:
                for key, value in env_content.items():
                    f.write(f"{key}={value}\n")
        except OSError as e:
            raise TokenStoreError(f"Could not write token env file {self.env_path}: {e}") from e


class UpstashRedisTokenStore(TokenStore):
    """Store tokens in Upstash Redis through the REST API."""

    def __init__(
        self,
        url: Optional[str] = None,
        token: Optional[str] = None,
        prefix: Optional[str] = None,
        timeout_seconds: float = 5.0,
    ):
        self.url = (url or os.getenv("UPSTASH_REDIS_REST_URL") or "").rstrip("/")
        self.token = token or os.getenv("UPSTASH_REDIS_REST_TOKEN") or ""
        self.prefix = prefix or os.getenv("TICKTICK_TOKEN_STORE_PREFIX", "ticktick_companion")
        self.timeout_seconds = timeout_seconds
        if not self.url or not self.token:
            raise TokenStoreError("Upstash Redis REST credentials are missing")

    def _key(self, name: str) -> str:
        return f"{self.prefix}:{name}"

    def _command(self, command) -> object:
        try:
            response = requests.post(
                self.url,
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "Content-Type": "application/json",
                },
                data=json.dumps(command),
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
        except requests.exceptions.RequestException as e:
            raise TokenStoreError(f"Upstash token store request failed: {e}") from e
        except ValueError as e:
            raise TokenStoreError("Upstash token store returned invalid JSON") from e
        if isinstance(payload, dict) and payload.get("error"):
            raise TokenStoreError(f"Upstash token store error: {payload['error']}")
        return payload.get("result") if isinstance(payload, dict) else None

    def load_tokens(self) -> Dict[str, str]:
        tokens: Dict[str, str] = {}
        access_token = self._command(["GET", self._key("access_token")])
        refresh_token = self._command(["GET", self._key("refresh_token")])
        if access_token:
            tokens["TICKTICK_ACCESS_TOKEN"] = str(access_token)
        if refresh_token:
            tokens["TICKTICK_REFRESH_TOKEN"] = str(refresh_token)
        return tokens

    def save_tokens(self, tokens: Dict[str, str]) -> None:
        if tokens.get("access_token"):
            self._command(["SET", self._key("access_token"), tokens["access_token"]])
        if tokens.get("refresh_token"):
            self._command(["SET", self._key("refresh_token"), tokens["refresh_token"]])


def default_token_store() -> TokenStore:
    """Prefer durable hosted token storage when Upstash credentials exist."""
    if os.getenv("UPSTASH_REDIS_REST_URL") and os.getenv("UPSTASH_REDIS_REST_TOKEN"):
        return UpstashRedisTokenStore()
    return EnvFileTokenStore()


def load_tokens_with_env_fallback(token_store: Optional[TokenStore] = None) -> Dict[str, str]:
    """Load tokens from the store first, then fall back to process/.env values."""
    load_dotenv()
    store = token_store or default_token_store()
    tokens: Dict[str, str] = {}
    try:
        tokens.update(store.load_tokens())
    except TokenStoreError as e:
        logger.warning("Could not load TickTick tokens from token store: %s", e)
    for key in TOKEN_KEYS:
        if not tokens.get(key) and os.getenv(key):
            tokens[key] = os.getenv(key, "")
    return tokens
