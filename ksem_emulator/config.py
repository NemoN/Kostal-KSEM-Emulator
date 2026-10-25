"""Runtime settings; site-specific values stay outside the repository."""

import math
import os
from dataclasses import dataclass, field
from pathlib import Path


def positive(name, default):
    value = float(os.environ.get(name, default))
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be finite and positive")
    return value


@dataclass(frozen=True)
class Config:
    source_type: str = "shelly"
    serial_port: str = "/dev/ksem"
    stale_seconds: float = 5.0
    mqtt_host: str = "localhost"
    mqtt_port: int = 1883
    mqtt_prefix: str = ""
    portal_id: str = ""
    service: str = "acload"
    instance: int = 40
    mqtt_username: str = ""
    mqtt_password: str = field(default="", repr=False)
    shelly_url: str = ""
    shelly_username: str = "admin"
    shelly_password: str = field(default="", repr=False)
    state_file: Path = Path("/data/energy-offsets.json")
    health_file: Path = Path("/tmp/ksem-health.json")

    @classmethod
    def from_env(cls):
        result = cls(
            source_type=os.getenv("SOURCE_TYPE", "shelly"),
            serial_port=os.getenv("SERIAL_PORT", "/dev/ksem"),
            stale_seconds=positive("STALE_SECONDS", "5"),
            mqtt_host=os.getenv("MQTT_HOST", "localhost"),
            mqtt_port=int(os.getenv("MQTT_PORT", "1883")),
            mqtt_prefix=os.getenv("MQTT_PREFIX", "").strip("/"),
            portal_id=os.getenv("VICTRON_PORTAL_ID", ""),
            service=os.getenv("VICTRON_SERVICE", "acload"),
            instance=int(os.getenv("VICTRON_INSTANCE", "40")),
            mqtt_username=os.getenv("MQTT_USERNAME", ""),
            mqtt_password=os.getenv("MQTT_PASSWORD", ""),
            shelly_url=os.getenv("SHELLY_URL", ""),
            shelly_username=os.getenv("SHELLY_USERNAME", "admin"),
            shelly_password=os.getenv("SHELLY_PASSWORD", ""),
            state_file=Path(os.getenv("STATE_FILE", "/data/energy-offsets.json")),
            health_file=Path(os.getenv("HEALTH_FILE", "/tmp/ksem-health.json")),
        )
        if result.source_type not in {"shelly", "victron_mqtt"}:
            raise ValueError("SOURCE_TYPE must be shelly or victron_mqtt")
        if not 1 <= result.mqtt_port <= 65535 or result.instance < 0:
            raise ValueError("Invalid MQTT port or device instance")
        if result.source_type == "shelly" and not result.shelly_url.startswith(("http://", "https://")):
            raise ValueError("Set SHELLY_URL to the Shelly /status URL")
        if result.source_type == "victron_mqtt":
            for name, value in [("VICTRON_PORTAL_ID", result.portal_id), ("VICTRON_SERVICE", result.service)]:
                if not value or any(c in value for c in "/+#"):
                    raise ValueError(f"Invalid {name}")
            if any(c in result.mqtt_prefix for c in "+#"):
                raise ValueError("MQTT_PREFIX must not contain wildcards")
        return result

    def topic(self, direction, suffix):
        return "/".join(p for p in [self.mqtt_prefix, direction, self.portal_id, suffix] if p)

    @property
    def meter(self):
        return f"{self.service}/{self.instance}"

    @property
    def source_id(self):
        return self.topic("N", self.meter)
