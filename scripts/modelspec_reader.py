"""Read a ModelSpec model's JSON form in either vocabulary.

A model's `modelspec` identifier decides its vocabulary: `1.0-draft` names the
record types `entities`, their members `properties` and a member's reference
`entity`; `1.0-draft-2` names them `records`, `fields` and `record`. Both are read.
A document whose keys disagree with its identifier, or that holds a removed or
reserved top-level word, is refused, as the reference CLI refuses it.

Only the model's own JSON form is read here. Representation descriptors and native
keys, which carry the `entity` and `property` fields of OpenVaultDB's frozen
representation contract, are not ModelSpec's keywords and do not pass through here.
"""

EARLIER = "1.0-draft"
CURRENT = "1.0-draft-2"
# identifier -> (record types, members of a record type, reference on a member)
VOCABULARY = {EARLIER: ("entities", "properties", "entity"), CURRENT: ("records", "fields", "record")}
REMOVED = ("collections", "recordsets")
RESERVED = ("projections", "migrations")


def _objects(value):
    """The object values of an object, or nothing when `value` is not an object."""
    return [item for item in value.values() if isinstance(item, dict)] if isinstance(value, dict) else []


def _wrong_key(identifier, other, key, expected):
    return ValueError(f'"{key}" belongs to format {other}; this document says "{identifier}", '
                      f'where the key is "{expected}"')


def _vocabulary(model):
    """Check the identifier against the keys and return the document's vocabulary."""
    if not isinstance(model, dict):
        raise ValueError("a ModelSpec JSON document must be an object")
    identifier = model.get("modelspec")
    if not isinstance(identifier, str) or identifier not in VOCABULARY:
        raise ValueError(f'"modelspec" is {identifier!r}; the defined values are "{CURRENT}" and "{EARLIER}"')
    for word in REMOVED:
        if word in model:
            raise ValueError(f'"{word}" was removed (decision 0019); a model cannot use it')
    for word in RESERVED:
        if word in model:
            raise ValueError(f'"{word}" is a reserved word with no content yet (decision 0019); a model cannot use it')
    vocabulary = VOCABULARY[identifier]
    other = EARLIER if identifier == CURRENT else CURRENT
    records, members, reference = vocabulary
    other_records, other_members, other_reference = VOCABULARY[other]
    if other_records in model:
        raise _wrong_key(identifier, other, other_records, records)
    for record_type in _objects(model.get(records)):
        if other_members in record_type:
            raise _wrong_key(identifier, other, other_members, members)
    # Components keep `fields` under both identifiers; their members' references follow the vocabulary.
    groups = [record_type.get(members) for record_type in _objects(model.get(records))]
    groups += [component.get("fields") for component in _objects(model.get("components"))]
    for group in groups:
        for member in _objects(group):
            if other_reference in member:
                raise _wrong_key(identifier, other, other_reference, reference)
    return vocabulary


def record_types(model):
    """Return the model's record types, keyed by name; empty when it declares none."""
    key = _vocabulary(model)[0]
    found = model.get(key, {})
    if not isinstance(found, dict):
        raise ValueError(f'"{key}" must be an object keyed by name')
    return found


def members(model, record_type):
    """Return the members of one record type of `model`, keyed by name."""
    key = _vocabulary(model)[1]
    found = record_type.get(key, {})
    if not isinstance(found, dict):
        raise ValueError(f'"{key}" must be an object keyed by name')
    return found
