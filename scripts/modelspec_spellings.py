"""Both spellings of a ModelSpec JSON model, and the exact rename between them.

`both` renames a parsed JSON model in memory. `renamed` and `model_state` are what the
repository's guards use to accept the model in exactly two states: the accepted file, or the
accepted file with exactly the three words renamed (`entity` to `record`, `property` to `field`,
and the JSON identifier and keys that go with them). The rename is recomputed here from the
accepted bytes; it is never read back from the tree being checked. Its output is pinned to
what the reference tool wrote: `modelspec rewrite` 0.2.0 turned the two accepted files into the
bytes whose SHA-256 are `RENAMED_SHA256`, so a rename that drifts from the tool cannot pass.
"""
import copy
import hashlib
import json
import re

PAIRS = (("entities", "records"), ("properties", "fields"), ("entity", "record"))

# The provider's model, JSON interchange copy and HCL source.
MODEL_PATHS = ("model/geonames.modelspec.json", "model/geonames.modelspec.hcl")
# SHA-256 of the output of `modelspec rewrite` 0.2.0 on the accepted files.
RENAMED_SHA256 = {
    "model/geonames.modelspec.json": "affd113f8ec5d20dcc4af38556e263528af71dc0e8410099fdbc3422461b18c2",
    "model/geonames.modelspec.hcl": "f1e7a466b8709bcb623efcb32a1c9b7c08e7e51d9313aa15142693ab9d958fb6",
}
# HCL block keywords, which the rename changes only where a line starts with one.
_HCL_WORDS = (("entity", "record"), ("property", "field"))


def _rename(model, old, new, identifier):
    result = copy.deepcopy(model)
    result["modelspec"] = identifier
    old_records, new_records = old[0], new[0]
    if old_records in result:
        result[new_records] = result.pop(old_records)
    for record_type in result.get(new_records, {}).values():
        if old[1] in record_type:
            record_type[new[1]] = record_type.pop(old[1])
        for member in record_type.get(new[1], {}).values():
            if old[2] in member:
                member[new[2]] = member.pop(old[2])
    for component in result.get("components", {}).values():
        for member in component.get("fields", {}).values():
            if old[2] in member:
                member[new[2]] = member.pop(old[2])
    return result


def both(model):
    """Return (earlier spelling, current spelling) of `model`, which may be in either."""
    old, new = tuple(zip(*PAIRS))
    return (_rename(model, new, old, "1.0-draft"), _rename(model, old, new, "1.0-draft-2"))


def _encode(model):
    return (json.dumps(model, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def _rename_json(data):
    model = json.loads(data)
    if _encode(model) != data:
        raise ValueError("the accepted model JSON is not in the formatting the rename preserves")
    return _encode(both(model)[1])


def _rename_hcl(data):
    lines = []
    for line in data.decode("utf-8").split("\n"):
        if line.lstrip().startswith("#"):
            lines.append(line)
            continue
        for old, new in _HCL_WORDS:
            found = re.match(rf'(\s*){old}(\s+")', line)
            if found:
                line = found.group(1) + new + line[found.end(1) + len(old):]
                break
        else:
            if re.match(r"\s*(entity|property|entities|properties)\b", line):
                raise ValueError("the HCL rename does not handle this construct: " + line.strip())
        lines.append(line)
    return "\n".join(lines).encode("utf-8")


def renamed(path, accepted):
    """The exact rename of the accepted bytes of one model file, checked against the tool's output."""
    if path not in RENAMED_SHA256:
        raise ValueError("not a renamed model file: " + path)
    result = _rename_json(accepted) if path.endswith(".json") else _rename_hcl(accepted)
    if hashlib.sha256(result).hexdigest() != RENAMED_SHA256[path]:
        raise ValueError("the rename does not reproduce the reference tool's output: " + path)
    return result


def model_state(accepted, current):
    """Return "accepted" or "renamed"; refuse anything else.

    `accepted` and `current` map each of MODEL_PATHS to bytes. Each current file must equal the
    accepted file or its exact rename, and the two files must be in the same state.
    """
    states = set()
    for path in MODEL_PATHS:
        if current[path] == accepted[path]:
            states.add("accepted")
        elif current[path] == renamed(path, accepted[path]):
            states.add("renamed")
        else:
            raise ValueError(f"model differs from the accepted file and from its exact rename: {path}")
    if len(states) != 1:
        raise ValueError("the model JSON and HCL are not in the same state")
    return states.pop()
