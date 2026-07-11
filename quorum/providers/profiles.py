"""Strict, non-executable provider profiles for audited remote adapters.

Profiles select a compiled provider adapter and an exact model.  They cannot
define endpoints, credentials, headers, request bodies, code, or tools.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


PROFILE_VERSION = 1
MAX_CONFIG_BYTES = 128 * 1024
ID_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,47}\Z")
MODEL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/+-]{0,119}\Z")
UPSTREAM_RE = re.compile(r"[a-z0-9][a-z0-9._/-]{0,79}\Z")
REASONING_LEVELS = frozenset({"provider_default", "none", "high", "max", "xhigh"})
PROFILE_KEYS = frozenset({"id", "label", "provider", "model", "reasoning", "upstream"})


class ProviderProfileError(ValueError):
    """A safe, non-secret-bearing profile validation failure."""


@dataclass(frozen=True)
class ProviderProfile:
    id: str
    label: str
    provider_id: str
    model: str
    reasoning: str = "provider_default"
    upstream: str | None = None
    built_in: bool = False

    def canonical(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "provider": self.provider_id,
            "model": self.model,
            "reasoning": self.reasoning,
            "upstream": self.upstream,
        }

    @property
    def fingerprint(self) -> str:
        body = json.dumps(
            self.canonical(), sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode("ascii")
        return hashlib.sha256(body).hexdigest()


def config_path() -> Path:
    root = os.environ.get("XDG_CONFIG_HOME")
    base = Path(root).expanduser() if root else Path.home() / ".config"
    return base / "quorum" / "providers.json"


def load_user_profiles(path: Path | None = None) -> tuple[ProviderProfile, ...]:
    target = path or config_path()
    try:
        data = _read_config_bytes(target)
    except FileNotFoundError:
        return ()
    try:
        document = json.loads(
            data.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ProviderProfileError("provider profile config is invalid JSON") from None
    if not isinstance(document, dict) or set(document) != {"version", "profiles"}:
        raise ProviderProfileError("provider profile config has an invalid schema")
    if document["version"] != PROFILE_VERSION or not isinstance(document["profiles"], list):
        raise ProviderProfileError("provider profile config has an unsupported version")
    if len(document["profiles"]) > 64:
        raise ProviderProfileError("provider profile config has too many profiles")
    profiles = tuple(_parse_profile(item) for item in document["profiles"])
    ids = [profile.id for profile in profiles]
    if len(ids) != len(set(ids)):
        raise ProviderProfileError("provider profile ids must be unique")
    return profiles


def write_user_profiles(
    profiles: Iterable[ProviderProfile], path: Path | None = None
) -> Path:
    target = path or config_path()
    profiles = tuple(profiles)
    document = {
        "version": PROFILE_VERSION,
        "profiles": [profile.canonical() for profile in profiles],
    }
    payload = json.dumps(document, indent=2, ensure_ascii=True) + "\n"
    if len(payload.encode("utf-8")) > MAX_CONFIG_BYTES:
        raise ProviderProfileError("provider profile config exceeds size limit")
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(target.parent, 0o700)
    except OSError:
        pass
    if target.exists() or target.is_symlink():
        _validate_config_file(target)
    temp = target.with_name(f".{target.name}.tmp-{os.getpid()}-{os.urandom(6).hex()}")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(temp, flags, 0o600)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            fd = -1
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, target)
        os.chmod(target, 0o600)
    finally:
        if fd >= 0:
            os.close(fd)
        try:
            temp.unlink()
        except FileNotFoundError:
            pass
    return target


def add_user_profile(profile: ProviderProfile, path: Path | None = None) -> Path:
    if profile.built_in:
        raise ProviderProfileError("cannot install a built-in profile")
    # Late import avoids making the schema module trust its own provider id.
    # The audited registry must accept the adapter and resolved policy first.
    from quorum.providers.remote import provider_snapshot
    provider_snapshot(profile)
    profiles = list(load_user_profiles(path))
    if any(item.id == profile.id for item in profiles):
        raise ProviderProfileError(f"provider profile already exists: {profile.id}")
    profiles.append(profile)
    return write_user_profiles(profiles, path)


def remove_user_profile(profile_id: str, path: Path | None = None) -> Path:
    profiles = list(load_user_profiles(path))
    kept = [profile for profile in profiles if profile.id != profile_id]
    if len(kept) == len(profiles):
        raise ProviderProfileError(f"unknown user provider profile: {profile_id}")
    return write_user_profiles(kept, path)


def make_user_profile(
    profile_id: str,
    provider_id: str,
    model: str,
    *,
    label: str | None = None,
    reasoning: str = "provider_default",
    upstream: str | None = None,
) -> ProviderProfile:
    return _parse_profile({
        "id": profile_id,
        "label": label or profile_id,
        "provider": provider_id,
        "model": model,
        "reasoning": reasoning,
        "upstream": upstream,
    })


def _parse_profile(item: object) -> ProviderProfile:
    if not isinstance(item, dict) or not set(item).issubset(PROFILE_KEYS):
        raise ProviderProfileError("provider profile has unknown or invalid fields")
    if not {"id", "provider", "model"}.issubset(item):
        raise ProviderProfileError("provider profile is missing required fields")
    profile_id = item["id"]
    provider_id = item["provider"]
    model = item["model"]
    label = item.get("label", profile_id)
    reasoning = item.get("reasoning", "provider_default")
    upstream = item.get("upstream")
    if not isinstance(profile_id, str) or not ID_RE.fullmatch(profile_id):
        raise ProviderProfileError("provider profile id is invalid")
    if not isinstance(provider_id, str) or not ID_RE.fullmatch(provider_id):
        raise ProviderProfileError("provider adapter id is invalid")
    if not isinstance(model, str) or not MODEL_RE.fullmatch(model):
        raise ProviderProfileError("provider model id is invalid")
    if not isinstance(label, str) or not 1 <= len(label) <= 80 or not _safe_text(label):
        raise ProviderProfileError("provider profile label is invalid")
    if not isinstance(reasoning, str) or reasoning not in REASONING_LEVELS:
        raise ProviderProfileError("provider reasoning level is invalid")
    if upstream is not None and (
        not isinstance(upstream, str) or not UPSTREAM_RE.fullmatch(upstream)
    ):
        raise ProviderProfileError("provider upstream route is invalid")
    if provider_id == "openrouter" and not upstream:
        raise ProviderProfileError("OpenRouter profiles require an exact upstream route")
    if provider_id != "openrouter" and upstream is not None:
        raise ProviderProfileError("upstream routing is only supported for OpenRouter")
    return ProviderProfile(profile_id, label, provider_id, model, reasoning, upstream)


def _safe_text(value: str) -> bool:
    return all(
        ord(char) >= 0x20 and ord(char) != 0x7f
        and unicodedata.category(char) not in {"Cc", "Cf", "Cs"}
        for char in value
    )


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ProviderProfileError("provider profile config has duplicate keys")
        result[key] = value
    return result


def _validate_config_file(path: Path) -> None:
    try:
        info = path.lstat()
    except OSError:
        raise ProviderProfileError("provider profile config is not readable") from None
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise ProviderProfileError("provider profile config must be a regular non-symlink file")
    if hasattr(os, "getuid") and info.st_uid != os.getuid():
        raise ProviderProfileError("provider profile config must be owned by the current user")
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise ProviderProfileError("provider profile config permissions must be 0600")


def _read_config_bytes(path: Path) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except FileNotFoundError:
        raise
    except OSError:
        raise ProviderProfileError("provider profile config is not a safe regular file") from None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise ProviderProfileError("provider profile config must be a regular non-symlink file")
        if hasattr(os, "getuid") and info.st_uid != os.getuid():
            raise ProviderProfileError("provider profile config must be owned by the current user")
        if stat.S_IMODE(info.st_mode) & 0o077:
            raise ProviderProfileError("provider profile config permissions must be 0600")
        if info.st_size > MAX_CONFIG_BYTES:
            raise ProviderProfileError("provider profile config exceeds size limit")
        with os.fdopen(fd, "rb") as handle:
            fd = -1
            data = handle.read(MAX_CONFIG_BYTES + 1)
        if len(data) > MAX_CONFIG_BYTES:
            raise ProviderProfileError("provider profile config exceeds size limit")
        return data
    finally:
        if fd >= 0:
            os.close(fd)
