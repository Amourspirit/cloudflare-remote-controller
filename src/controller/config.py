from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ControllerConfig(BaseSettings):
	model_config = SettingsConfigDict(
		env_file=".env",
		env_file_encoding="utf-8",
		extra="ignore",
		case_sensitive=True,
	)

	cf_api_token: SecretStr = Field(alias="CF_API_TOKEN")
	cf_zone_id: str | None = Field(default=None, alias="CF_ZONE_ID")
	cf_managed_zone_ids: str = Field(default="", alias="CF_MANAGED_ZONE_IDS")
	cf_tunnel_cname: str | None = Field(default=None, alias="CF_TUNNEL_CNAME")
	reconcile_interval_seconds: float = Field(
		default=120, gt=0, alias="RECONCILE_INTERVAL_SECONDS"
	)
	event_debounce_ms: int = Field(default=500, ge=0, alias="EVENT_DEBOUNCE_MS")
	allow_target_mismatch: bool = Field(default=False, alias="ALLOW_TARGET_MISMATCH")
	log_level: str = Field(default="INFO", alias="LOG_LEVEL")
	docker_socket_path: Path = Field(
		default=Path("/var/run/docker.sock"), alias="DOCKER_SOCKET_PATH"
	)
	cloudflare_api_base_url: str = Field(
		default="https://api.cloudflare.com/client/v4", alias="CLOUDFLARE_API_BASE_URL"
	)
	cloudflare_timeout_seconds: float = Field(
		default=20, gt=0, alias="CLOUDFLARE_TIMEOUT_SECONDS"
	)
	cloudflare_max_attempts: int = Field(
		default=3, ge=1, alias="CLOUDFLARE_MAX_ATTEMPTS"
	)
	heartbeat_path: Path = Field(
		default=Path("/tmp/cfroute-controller-heartbeat"), alias="HEARTBEAT_PATH"
	)

	@field_validator("cf_zone_id", "cf_tunnel_cname", mode="before")
	@classmethod
	def empty_string_is_none(cls, value: object) -> object:
		if isinstance(value, str) and not value.strip():
			return None
		return value

	@field_validator("log_level")
	@classmethod
	def normalize_log_level(cls, value: str) -> str:
		normalized = value.upper()
		if normalized not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
			raise ValueError(f"unsupported log level: {value}")
		return normalized

	@property
	def managed_zone_ids(self) -> set[str]:
		zone_ids = {
			zone_id.strip()
			for zone_id in self.cf_managed_zone_ids.replace(";", ",").split(",")
			if zone_id.strip()
		}
		if self.cf_zone_id is not None:
			zone_ids.add(self.cf_zone_id)
		return zone_ids
