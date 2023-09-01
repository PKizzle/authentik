"""authentik core config loader"""
import os
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
from glob import glob
from json import JSONEncoder, dumps, loads
from json.decoder import JSONDecodeError
from pathlib import Path
from string import Template
from sys import argv, stderr
from time import time
from typing import Any, Optional
from urllib.parse import quote_plus, urlencode, urlparse

import yaml
from django.conf import ImproperlyConfigured

SEARCH_PATHS = ["authentik/lib/default.yml", "/etc/authentik/config.yml", ""] + glob(
    "/etc/authentik/config.d/*.yml", recursive=True
)
ENV_PREFIX = "AUTHENTIK"
ENVIRONMENT = os.getenv(f"{ENV_PREFIX}_ENV", "local")

REDIS_ENV_KEYS = [
    f"{ENV_PREFIX}_REDIS__HOST",
    f"{ENV_PREFIX}_REDIS__PORT" f"{ENV_PREFIX}_REDIS__DB",
    f"{ENV_PREFIX}_REDIS__USERNAME",
    f"{ENV_PREFIX}_REDIS__PASSWORD",
    f"{ENV_PREFIX}_REDIS__TLS",
    f"{ENV_PREFIX}_REDIS__TLS_REQS",
]

DEPRECATIONS = {
    "redis.broker_url": "broker.url",
    "redis.broker_transport_options": "broker.transport_options",
    "redis.cache_timeout": "cache.timeout",
    "redis.cache_timeout_flows": "cache.timeout_flows",
    "redis.cache_timeout_policies": "cache.timeout_policies",
    "redis.cache_timeout_reputation": "cache.timeout_reputation",
}


def get_path_from_dict(root: dict, path: str, sep=".", default=None) -> Any:
    """Recursively walk through `root`, checking each part of `path` split by `sep`.
    If at any point a dict does not exist, return default"""
    for comp in path.split(sep):
        if root and comp in root:
            root = root.get(comp)
        else:
            return default
    return root


@dataclass
class Attr:
    """Single configuration attribute"""

    class Source(Enum):
        """Sources a configuration attribute can come from, determines what should be done with
        Attr.source (and if it's set at all)"""

        UNSPECIFIED = "unspecified"
        ENV = "env"
        CONFIG_FILE = "config_file"
        URI = "uri"

    value: Any

    source_type: Source = field(default=Source.UNSPECIFIED)

    # depending on source_type, might contain the environment variable or the path
    # to the config file containing this change or the file containing this value
    source: Optional[str] = field(default=None)


class AttrEncoder(JSONEncoder):
    """JSON encoder that can deal with `Attr` classes"""

    def default(self, o: Any) -> Any:
        if isinstance(o, Attr):
            return o.value
        return super().default(o)


class UNSET:
    """Used to test whether configuration key has not been set."""


class ConfigLoader:
    """Search through SEARCH_PATHS and load configuration. Environment variables starting with
    `ENV_PREFIX` are also applied.

    A variable like AUTHENTIK_POSTGRESQL__HOST would translate to postgresql.host"""

    def __init__(self, **kwargs):
        super().__init__()
        self.__config = {}
        base_dir = Path(__file__).parent.joinpath(Path("../..")).resolve()
        for _path in SEARCH_PATHS:
            path = Path(_path)
            # Check if path is relative, and if so join with base_dir
            if not path.is_absolute():
                path = base_dir / path
            if path.is_file() and path.exists():
                # Path is an existing file, so we just read it and update our config with it
                self.update_from_file(path)
            elif path.is_dir() and path.exists():
                # Path is an existing dir, so we try to read the env config from it
                env_paths = [
                    path / Path(ENVIRONMENT + ".yml"),
                    path / Path(ENVIRONMENT + ".env.yml"),
                    path / Path(ENVIRONMENT + ".yaml"),
                    path / Path(ENVIRONMENT + ".env.yaml"),
                ]
                for env_file in env_paths:
                    if env_file.is_file() and env_file.exists():
                        # Update config with env file
                        self.update_from_file(env_file)
        self.update_from_env()
        self.update(self.__config, kwargs)
        self.check_deprecations()
        self.update_redis_url()

    # pylint: disable=too-many-statements
    def update_redis_url(self):
        """Build Redis URL from other env variables"""

        redis_url = "redis"
        redis_url_query = {}
        redis_host = "localhost"
        redis_port = 6379
        redis_db = 0

        if self.get("redis.url", UNSET) is not UNSET:
            env = {k: quote_plus(v) for k, v in os.environ.items() if k in REDIS_ENV_KEYS}
            self.set("redis.url", Template(self.get("redis.url", UNSET)).substitute(env))
        else:
            self.log(
                "warning",
                "Other Redis environment variables have been deprecated "
                "in favor of 'AUTHENTIK_REDIS__URL'! "
                "Please update your configuration.",
            )
            if self.get("redis.host", UNSET) is not UNSET:
                redis_host = self.get("redis.host")
            if self.get("redis.port", UNSET) is not UNSET:
                redis_port = int(self.get("redis.port"))
            redis_addr = f"{redis_host}:{redis_port}"
            if self.get_bool("redis.tls", False):
                redis_url = "rediss"
            if self.get("redis.tls_reqs", UNSET) is not UNSET:
                redis_tls_reqs = self.get("redis.tls_reqs")
                match redis_tls_reqs.lower():
                    case "none":
                        redis_url_query.pop("skipverify", None)
                        redis_url_query["insecureskipverify"] = "true"
                    case "optional":
                        redis_url_query.pop("insecureskipverify", None)
                        redis_url_query["skipverify"] = "true"
                    case "required":
                        pass
                    case _:
                        self.log(
                            "warning",
                            f"Unsupported Redis TLS requirements option '{redis_tls_reqs}'! "
                            "Using default option 'required'.",
                        )
            if self.get("redis.db", UNSET) is not UNSET:
                redis_db = int(self.get("redis.db"))
            if self.get("redis.username", UNSET) is not UNSET:
                redis_url_query["username"] = self.get("redis.username")
            if self.get("redis.password", UNSET) is not UNSET:
                redis_url_query["password"] = self.get("redis.password")
            redis_url += f"://{redis_addr}"
            if redis_db is not None:
                redis_url += f"/{redis_db}"
            # Sort query in order to have similar tests between Go and Python implementation
            redis_url_query = urlencode(dict(sorted(redis_url_query.items())))
            if redis_url_query != "":
                redis_url += f"?{redis_url_query}"
            self.set("redis.url", redis_url)

    def check_deprecations(self):
        """Warn if any deprecated configuration options are used"""

        def _pop_deprecated_key(current_obj, dot_parts, index):
            """Recursive function to remove deprecated keys in configuration"""
            dot_part = dot_parts[index]
            if index == len(dot_parts) - 1:
                return current_obj.pop(dot_part)
            value = _pop_deprecated_key(current_obj[dot_part], dot_parts, index + 1)
            if not current_obj[dot_part]:
                current_obj.pop(dot_part)
            return value

        for deprecation, replacement in DEPRECATIONS.items():
            if self.get(deprecation, default=UNSET) is not UNSET:
                self.log(
                    "warning",
                    f"'{deprecation}' has been deprecated in favor of '{replacement}'! "
                    "Please update your configuration.",
                )

                deprecated_attr = _pop_deprecated_key(self.__config, deprecation.split("."), 0)
                self.set(replacement, deprecated_attr.value)

    def log(self, level: str, message: str, **kwargs):
        """Custom Log method, we want to ensure ConfigLoader always logs JSON even when
        'structlog' or 'logging' hasn't been configured yet."""
        output = {
            "event": message,
            "level": level,
            "logger": self.__class__.__module__,
            "timestamp": time(),
        }
        output.update(kwargs)
        print(dumps(output), file=stderr)

    def update(self, root: dict[str, Any], updatee: dict[str, Any]) -> dict[str, Any]:
        """Recursively update dictionary"""
        for key, value in updatee.items():
            if isinstance(value, Mapping):
                root[key] = self.update(root.get(key, {}), value)
            else:
                if isinstance(value, str):
                    value = self.parse_uri(value)
                elif isinstance(value, Attr) and isinstance(value.value, str):
                    value = self.parse_uri(value.value)
                elif not isinstance(value, Attr):
                    value = Attr(value)
                root[key] = value
        return root

    def refresh(self, key: str):
        """Update a single value"""
        attr: Attr = get_path_from_dict(self.raw, key)
        if attr.source_type != Attr.Source.URI:
            return
        attr.value = self.parse_uri(attr.source).value

    def parse_uri(self, value: str) -> Attr:
        """Parse string values which start with a URI"""
        url = urlparse(value)
        parsed_value = value
        if url.scheme == "env":
            parsed_value = os.getenv(url.netloc, url.query)
        if url.scheme == "file":
            try:
                with open(url.path, "r", encoding="utf8") as _file:
                    parsed_value = _file.read().strip()
            except OSError as exc:
                self.log("error", f"Failed to read config value from {url.path}: {exc}")
                parsed_value = url.query
        return Attr(parsed_value, Attr.Source.URI, value)

    def update_from_file(self, path: Path):
        """Update config from file contents"""
        try:
            with open(path, encoding="utf8") as file:
                try:
                    self.update(self.__config, yaml.safe_load(file))
                    self.log("debug", "Loaded config", file=str(path))
                except yaml.YAMLError as exc:
                    raise ImproperlyConfigured from exc
        except PermissionError as exc:
            self.log(
                "warning",
                "Permission denied while reading file",
                path=path,
                error=str(exc),
            )

    def update_from_dict(self, update: dict):
        """Update config from dict"""
        self.__config.update(update)

    @staticmethod
    def _set_value_for_key_path(outer, path, value, sep="."):
        # Recursively convert path from a.b.c into outer[a][b][c]
        current_obj = outer
        dot_parts = path.split(sep)
        for dot_part in dot_parts[:-1]:
            current_obj.setdefault(dot_part, {})
            current_obj = current_obj[dot_part]
        current_obj[dot_parts[-1]] = Attr(value, Attr.Source.ENV, path)
        return outer

    def update_from_env(self):
        """Check environment variables"""
        outer = {}
        idx = 0
        for key, value in os.environ.items():
            if not key.startswith(ENV_PREFIX):
                continue
            relative_key = key.replace(f"{ENV_PREFIX}_", "", 1).replace("__", ".").lower()
            # Check if the value is json, and try to load it
            try:
                value = loads(value)
            except JSONDecodeError:
                pass
            outer = self._set_value_for_key_path(outer, relative_key, value)
            idx += 1
        if idx > 0:
            self.log("debug", "Loaded environment variables", count=idx)
            self.update(self.__config, outer)

    @contextmanager
    def patch(self, path: str, value: Any):
        """Context manager for unittests to patch a value"""
        original_value = self.get(path)
        self.set(path, value)
        try:
            yield
        finally:
            self.set(path, original_value)

    @property
    def raw(self) -> dict:
        """Get raw config dictionary"""
        return self.__config

    def get(self, path: str, default=None, sep=".") -> Any:
        """Access attribute by using yaml path"""
        # Walk sub_dicts before parsing path
        root = self.raw
        # Walk each component of the path
        attr: Attr = get_path_from_dict(root, path, sep=sep, default=Attr(default))
        return attr.value

    def get_int(self, path: str, default=0) -> int:
        """Wrapper for get that converts value into int"""
        try:
            return int(self.get(path, default))
        except ValueError as exc:
            self.log("warning", "Failed to parse config as int", path=path, exc=str(exc))
            return default

    def get_bool(self, path: str, default=False) -> bool:
        """Wrapper for get that converts value into boolean"""
        return str(self.get(path, default)).lower() == "true"

    def set(self, path: str, value: Any, sep="."):
        """Set value using same syntax as get()"""
        # Walk sub_dicts before parsing path
        self._set_value_for_key_path(self.raw, path, value, sep)


CONFIG = ConfigLoader()


if __name__ == "__main__":
    if len(argv) < 2:
        print(dumps(CONFIG.raw, indent=4, cls=AttrEncoder))
    else:
        print(CONFIG.get(argv[1]))
