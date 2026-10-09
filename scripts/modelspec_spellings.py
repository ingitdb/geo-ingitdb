"""Both spellings of a ModelSpec JSON model, whichever one a file is written in."""
import copy

PAIRS = (("entities", "records"), ("properties", "fields"), ("entity", "record"))


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
