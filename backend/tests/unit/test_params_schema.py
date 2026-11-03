import pytest
from pydantic import ValidationError

from hephaistos.adapters.base import AdapterError
from hephaistos.adapters.idol import IdolAdapter, IdolParams
from hephaistos.adapters.registry import build_registry
from hephaistos.adapters.schema import effective_params, schema_violations
from hephaistos.domain.errors import ErrorCode

ADAPTERS = build_registry(enable_fake=True)


def field(**keywords):
    """Schéma d'objet minimal à un champ, conforme sauf ce que le test y ajoute."""
    prop = {"type": "integer", "title": "N", "x-ui": {"widget": "slider", "order": 1}, **keywords}
    return {"type": "object", "title": "P", "additionalProperties": False, "properties": {"n": prop}}


# ------------------------------------------------------------------------- le vérificateur lui-même


def test_minimal_schema_is_accepted():
    assert schema_violations(field(minimum=1, maximum=9, default=3, multipleOf=1)) == []


def test_nullable_field_is_accepted():
    schema = field()
    prop = schema["properties"]["n"]
    schema["properties"]["n"] = {
        "anyOf": [{"type": "string", "enum": ["a", "b"]}, {"type": "null"}],
        "default": None,
        "title": "N",
        "x-ui": prop["x-ui"],
    }
    assert schema_violations(schema) == []


@pytest.mark.parametrize(
    ("extra", "expected"),
    [
        ({"pattern": "^a$"}, "pattern"),
        ({"format": "email"}, "format"),
        ({"type": "array"}, "array"),
        ({"type": "object"}, "object"),
        ({"$ref": "#/$defs/X"}, "$ref"),
    ],
)
def test_keywords_outside_the_subset_are_refused(extra, expected):
    violations = schema_violations(field(**extra))
    assert any(expected in v for v in violations), violations


def test_extra_properties_must_be_forbidden():
    schema = field()
    schema["additionalProperties"] = True
    assert any("additionalProperties" in v for v in schema_violations(schema))


def test_definitions_are_refused():
    schema = field()
    schema["$defs"] = {"X": {"type": "string"}}
    assert any("$defs" in v for v in schema_violations(schema))


def test_missing_x_ui_is_refused():
    schema = field()
    del schema["properties"]["n"]["x-ui"]
    assert any("x-ui" in v for v in schema_violations(schema))


@pytest.mark.parametrize(
    "x_ui", [{"widget": "colour", "order": 1}, {"widget": "slider"}, {"widget": "slider", "order": 1, "z": 1}]
)
def test_invalid_x_ui_is_refused(x_ui):
    schema = field()
    schema["properties"]["n"]["x-ui"] = x_ui
    assert schema_violations(schema)


def test_duplicate_order_is_refused():
    schema = field()
    schema["properties"]["m"] = dict(schema["properties"]["n"])
    assert any("order" in v for v in schema_violations(schema))


# ------------------------------------------------------------------------- les adaptateurs déclarés


@pytest.mark.parametrize("adapter_id", sorted(ADAPTERS))
def test_every_adapter_schema_stays_in_the_subset(adapter_id):
    schema = ADAPTERS[adapter_id].params_model.model_json_schema()
    assert schema_violations(schema) == []


# ------------------------------------------------------------------------- Idol : docs/specifications.md §3.2


def test_idol_defaults_are_the_published_ones():
    assert IdolParams().model_dump() == {
        "mode": "synthetic",
        "steps": 50,
        "cfg_strength": 3.0,
        "rescale_t": 3.0,
        "num_gaussians": 262144,
        "crop_margin": None,
    }


def test_only_the_image_type_is_a_simple_setting():
    properties = IdolParams.model_json_schema()["properties"]
    simple = {name for name, prop in properties.items() if not prop["x-ui"].get("advanced")}
    assert simple == {"mode"}


@pytest.mark.parametrize(
    "overrides",
    [
        {"steps": 9},
        {"steps": 101},
        {"cfg_strength": -0.1},
        {"cfg_strength": 10.1},
        {"rescale_t": 0.9},
        {"rescale_t": 6.1},
        {"num_gaussians": 32768},
        {"num_gaussians": 200000},
        {"mode": "photo"},
        {"crop_margin": 0.9},
        {"crop_margin": 2.6},
    ],
)
def test_idol_refuses_values_outside_the_published_ranges(overrides):
    with pytest.raises(ValidationError):
        IdolParams.model_validate(overrides)


@pytest.mark.parametrize(
    "overrides",
    [
        {"steps": 10},
        {"steps": 100},
        {"cfg_strength": 0.0},
        {"rescale_t": 6.0},
        {"num_gaussians": 65536},
        {"crop_margin": None},
        {"crop_margin": 1.0},
        {"crop_margin": 2.5},
    ],
)
def test_idol_accepts_the_published_extremes(overrides):
    IdolParams.model_validate(overrides)


def test_every_slider_position_is_accepted():
    # multiple_of=0.1 sur des flottants : 0.3 n'est pas exactement 3 * 0.1 en binaire. Chaque
    # position que propose le curseur doit passer, sinon le formulaire produirait des refus.
    for tenths in range(0, 101):
        IdolParams(cfg_strength=round(tenths * 0.1, 1))
    for tenths in range(10, 61):
        IdolParams(rescale_t=round(tenths * 0.1, 1))


def test_schema_carries_no_developer_notes():
    assert "description" not in IdolParams.model_json_schema()


# ------------------------------------------------------------------------- paramètres effectifs (architecture §7)


def test_effective_params_are_defaults_then_preset_then_input():
    params = effective_params(IdolAdapter(), "fast", {"steps": 30})
    assert params.model_dump() == {
        "mode": "synthetic",
        "steps": 30,  # saisie > préréglage (25)
        "cfg_strength": 3.0,  # défaut
        "rescale_t": 3.0,
        "num_gaussians": 131072,  # préréglage
        "crop_margin": None,
    }


def test_effective_params_without_preset_are_defaults_plus_input():
    assert effective_params(IdolAdapter(), None, {"mode": "real"}).model_dump()["mode"] == "real"


def test_unknown_preset_is_params_invalid():
    with pytest.raises(AdapterError) as caught:
        effective_params(IdolAdapter(), "ultra", {})
    assert caught.value.code is ErrorCode.PARAMS_INVALID


def test_invalid_input_is_params_invalid_and_names_the_field():
    with pytest.raises(AdapterError) as caught:
        effective_params(IdolAdapter(), "balanced", {"steps": 500})
    assert caught.value.code is ErrorCode.PARAMS_INVALID
    assert "steps" in caught.value.message
