"""Validate contracts before inference and outputs before they are published.

This is validation, not grammar-constrained token sampling. Constrained outputs
are buffered so an invalid result can never be published as a successful call.
Schemas may only reference their own document; resolving network/files is forbidden.
"""
import json

from jsonschema import Draft202012Validator, ValidationError
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012
from lark import Lark, LarkError
import regex

from .errors import APIError


def loads(text):
    def constant(value):
        raise ValueError(f"non-JSON number {value}")

    def object_pairs(pairs):
        obj = {}
        for k, v in pairs:
            if k in obj:
                raise ValueError(f"duplicate JSON key {k}")
            obj[k] = v
        return obj

    return json.loads(text, parse_constant=constant, object_pairs_hook=object_pairs)


def schema_validator(schema, param):
    if not isinstance(schema, dict):
        raise APIError("schema must be an object", param)

    def local_refs(value):
        if isinstance(value, dict):
            for k, v in value.items():
                if k in ("$ref", "$dynamicRef") and (not isinstance(v, str) or not v.startswith("#")):
                    raise APIError("only document-local schema references are supported", param)
                if k == "$id":
                    raise APIError("schema resource identifiers are not supported", param)
                local_refs(v)
        elif isinstance(value, list):
            for v in value:
                local_refs(v)

    local_refs(schema)
    try:
        Draft202012Validator.check_schema(schema)
        resource = Resource.from_contents(schema, default_specification=DRAFT202012)
        registry = Registry().with_resource("urn:strata:schema", resource)
        resolver = registry.resolver_with_root(resource)
        validator = Draft202012Validator(schema, registry=registry)
        # Resolve even references in branches that the first completion might not use.
        def refs(value):
            if isinstance(value, dict):
                for k, v in value.items():
                    if k in ("$ref", "$dynamicRef"):
                        resolver.lookup(v)
                    else:
                        refs(v)
            elif isinstance(value, list):
                for v in value:
                    refs(v)
        refs(schema)
        return validator
    except APIError:
        raise
    except Exception as e:
        raise APIError(f"invalid JSON schema: {e}", param) from e


def validate_schema(validator, value):
    try:
        validator.validate(value)
    except ValidationError as e:
        raise APIError(f"model output violates its schema: {e.message}", code="invalid_model_output", status=500) from e


class Grammar:
    def __init__(self, specification, param):
        if not isinstance(specification, dict):
            raise APIError("grammar must be an object", param)
        syntax, definition = specification.get("syntax"), specification.get("definition")
        if not isinstance(definition, str) or not definition or len(definition) > 65536:
            raise APIError("grammar definition must be a nonempty string of at most 65536 characters", param)
        self.syntax = syntax
        try:
            if syntax == "lark":
                # Never permit Lark to import arbitrary files from the server's filesystem.
                for line in definition.splitlines():
                    if line.strip().startswith("%import") and not regex.fullmatch(
                            r"\s*%import common\.[A-Za-z_]+(?:\s*->\s*[A-Za-z_]+)?\s*", line):
                        raise APIError("only bundled Lark common terminal imports are supported", param)
                # Lark's runtime lexer forbids empty regex terminals. Codex's patch
                # grammar uses /(.*)/ for an optional line body; express that same
                # language with an optional, nonempty terminal (dot excludes LF).
                definition = definition.replace("/(.*)/", "/[^\\n]+/?")
                self.parser = Lark(definition, parser="lalr", start="start")
            elif syntax == "regex":
                self.parser = regex.compile(definition)
            else:
                raise APIError("grammar syntax must be lark or regex", param)
        except (LarkError, regex.error) as e:
            raise APIError(f"invalid grammar: {e}", param) from e

    def validate(self, text):
        try:
            if self.syntax == "lark":
                self.parser.parse(text)
            elif self.parser.fullmatch(text, timeout=0.25) is None:
                raise ValueError("regex does not match")
        except (LarkError, ValueError, TimeoutError) as e:
            raise APIError("model output violates the custom tool grammar", code="invalid_model_output", status=500) from e
