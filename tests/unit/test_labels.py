import pytest

from controller.config import ControllerConfig
from controller.labels import LabelValidationError, parse_container_routes


def config(**overrides: object) -> ControllerConfig:
	values = {
		"CF_API_TOKEN": "secret-token",
		"CF_ZONE_ID": "default-zone",
		"CF_TUNNEL_CNAME": "default.cfargotunnel.com",
	}
	values.update(overrides)
	return ControllerConfig.model_validate(values)


def test_disabled_container_has_no_routes() -> None:
	assert parse_container_routes("container-1", {}, config()) == ()


def test_parses_normalizes_and_deduplicates_names() -> None:
	routes = parse_container_routes(
		"container-1",
		{
			"cfroute.enabled": "true",
			"cfroute.names": "App.Example.com.,api.example.com;app.example.com",
			"com.docker.compose.project": "demo",
			"com.docker.compose.service": "web",
		},
		config(),
	)

	assert [route.key.name for route in routes] == [
		"app.example.com",
		"api.example.com",
	]
	assert all(route.key.zone_id == "default-zone" for route in routes)
	assert all(route.target == "default.cfargotunnel.com" for route in routes)
	assert all(route.proxied and route.delete_on_stop for route in routes)
	assert routes[0].owner.compose_project == "demo"
	assert routes[0].owner.compose_service == "web"


def test_label_values_override_controller_defaults() -> None:
	(route,) = parse_container_routes(
		"container-1",
		{
			"cfroute.enabled": "true",
			"cfroute.names": "app.example.com",
			"cfroute.zone_id": "override-zone",
			"cfroute.tunnel_cname": "override.cfargotunnel.com",
			"cfroute.proxied": "false",
			"cfroute.delete_on_stop": "false",
		},
		config(),
	)

	assert route.key.zone_id == "override-zone"
	assert route.target == "override.cfargotunnel.com"
	assert route.proxied is False
	assert route.delete_on_stop is False


@pytest.mark.parametrize(
	("labels", "message"),
	[
		({"cfroute.enabled": "yes"}, "cfroute.enabled must be true or false"),
		(
			{"cfroute.enabled": "true", "cfroute.names": "not-a-domain"},
			"invalid DNS name",
		),
		(
			{
				"cfroute.enabled": "true",
				"cfroute.names": "app.example.com",
				"cfroute.proxied": "yes",
			},
			"cfroute.proxied must be true or false",
		),
	],
)
def test_rejects_malformed_opt_in_labels(
	labels: dict[str, str], message: str
) -> None:
	with pytest.raises(LabelValidationError, match=message):
		parse_container_routes("container-1", labels, config())


def test_requires_zone_and_target_from_label_or_environment() -> None:
	labels = {"cfroute.enabled": "true", "cfroute.names": "app.example.com"}

	with pytest.raises(LabelValidationError, match="cfroute.zone_id is required"):
		parse_container_routes(
			"container-1", labels, config(CF_ZONE_ID=None, CF_TUNNEL_CNAME=None)
		)


def test_managed_zone_ids_include_default_and_explicit_cleanup_zones() -> None:
	settings = config(CF_MANAGED_ZONE_IDS="zone-2, zone-3;zone-2")

	assert settings.managed_zone_ids == {"default-zone", "zone-2", "zone-3"}
