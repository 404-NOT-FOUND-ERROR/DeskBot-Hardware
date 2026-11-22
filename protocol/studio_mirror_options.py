"""Resource mirror choices for authoring v2 and deterministic package metadata."""
from protocol import studio_role_layout as layouts

SCHEMA = "deskbot.studio_draft/v2"
FILE = "deskbot-layout.json"
CAPABILITY = "asset_layout_manifest_v1"


def validate(overrides, catalog):
    if type(overrides) is not dict:
        raise ValueError("镜像设置格式无效")
    for resource, value in overrides.items():
        if (resource not in catalog["assets"] or type(value) is not bool
                or catalog["assets"][resource]["object_kind"] == "listen"):
            raise ValueError("镜像设置应对应已知动画与布尔值")


def manifest(role, catalog):
    requirements = layouts.load_requirements(catalog)
    assets = []
    for resource in sorted(set(role["bindings"].values()) - {None}):
        semantic = next(s for s, name in role["bindings"].items() if name == resource)
        asset = catalog["assets"][resource]
        manual = resource in role["mirror_overrides"]
        playback = layouts.playback(catalog, semantic, resource, requirements,
                                   mirror_override=role["mirror_overrides"].get(resource))
        assets.append(dict(resource_id=resource, resource_sha256=asset["deployed_sha256"],
            width=asset["frame_width"], height=asset["frame_height"], layout_id=playback["layout_id"],
            mirror=playback["layout_id"] == "eye_anim", source="manual" if manual else "automatic"))
    return dict(schema="deskbot.asset_layouts/v1", role_id=role["id"], assets=assets)
