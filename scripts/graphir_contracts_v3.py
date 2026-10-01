"""Lower one public function to one Compute; wiring comes only from its interface."""
from pathlib import Path

from schema_validation import load_schema, validate_schema

SCHEMA_V3 = Path(__file__).resolve().parents[1] / 'schemas/graphir-contracts-v3.schema.json'


def lower_contracts_v3(document, params, return_type):
    errors = validate_schema(document, load_schema(SCHEMA_V3))
    if errors:
        raise ValueError('; '.join(errors))
    result = 'result'
    suffix = 0
    while result in params:
        result = f'result_{suffix}'
        suffix += 1
    return {
        'contract_version': '1.0',
        'interface': document['interface'],
        'steps': [{
            'kind': 'Compute',
            'description': document['compute']['description'],
            'needs': list(params),
            'produces': {result: return_type},
        }],
        'return': result,
    }
