"""Installed, credential-free discovery of the supported provider profiles."""

from copy import deepcopy

from zai_arsenkin.adapter import PROFILES, build_payload

EXAMPLES = {
    "check-top": {"queries": ["кофемашина для офиса"], "se": [{"type": 1, "region": 213}], "depth": 10},
    "clustering": {"queries": ["кофемашина для офиса", "офисная кофемашина"], "se": 1, "region": 213},
    "wordstat-frequency": {"queries": ["кофемашина"], "regions": [213], "ws": ["base"]},
    "wordstat-phrases": {"queries": ["кофемашина"], "region": 213},
    "wordstat-dynamics": {
        "queries": ["кофемашина"],
        "region": 213,
        "group": "month",
        "startdate": "2026-01-01",
        "enddate": "2026-06-30",
    },
}


def profile_catalog():
    profiles = []
    for name, profile in PROFILES.items():
        example = deepcopy(EXAMPLES.get(name))
        if example is not None:
            build_payload(name, example)
        profiles.append(
            {
                "profile": name,
                "provider_tool": profile.tools_name,
                "required": list(profile.required),
                "defaults": deepcopy(profile.defaults),
                "list_fields": list(profile.list_fields),
                "boolean_fields": list(profile.bool_fields),
                "max_items": dict(profile.max_items),
                "example_payload": example,
            }
        )
    return {
        "profiles": profiles,
        "provider_calls": 0,
        "workflow": [
            "arsenkin_estimate",
            "arsenkin_prepare",
            "operator acceptance",
            "arsenkin_submit",
            "worker",
            "arsenkin_get_result",
            "arsenkin_result_table",
        ],
        "estimates": "server policy units; not a provider price quote",
        "saved_views": ["serp_urls", "serp_hosts", "clusters"],
        "documentation": "https://help.arsenkin.ru/api",
    }
