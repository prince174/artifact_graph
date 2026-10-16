"""Small map payloads; full evidence remains available through node details."""

MAP_FIELDS = frozenset((
    "id", "kind", "label", "active", "hasTargetOutput", "hasSbom", "hasImagePush",
    "status", "state", "searchMatch", "stale", "visualReason",
))


def compact_node(node):
    result = {key: value for key, value in node.items() if key in MAP_FIELDS}
    result["detailsDeferred"] = True
    for field, keys in (("pushedImages", ("image", "digest", "sourcePaths")),
                        ("sbomArtifacts", ("path", "sbomStatus"))):
        if field in node:
            result[field] = [{key: item[key] for key in keys if key in item} for item in node[field]]
    return result
