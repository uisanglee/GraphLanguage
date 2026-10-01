"""Lower positional references and shared record shapes; no model code executes."""
import copy
from pathlib import Path

from schema_validation import load_schema, validate_schema

SCHEMA_V2 = Path(__file__).resolve().parents[1] / 'schemas/graphir-contracts-v2.schema.json'


def shape_type(shape):
    from graphir_core import normalize_type
    if isinstance(shape, str):
        return normalize_type(shape)
    if shape['kind'] == 'list':
        return 'list[' + shape_type(shape['items']) + ']'
    return 'dict[str, Any]'


def shape_structure(shape):
    if isinstance(shape, str):
        return shape_type(shape)
    if shape['kind'] == 'list':
        return ('list', shape_structure(shape['items']))
    return ('record', sorted((k, shape_structure(v['shape'])) for k, v in shape['fields'].items()))


def lower_contracts_v2(document, params):
    errors = validate_schema(document, load_schema(SCHEMA_V2))
    if errors:
        raise ValueError('; '.join(errors))
    shapes = {}
    reserved = set(params)
    counter = 0

    def fresh():
        nonlocal counter
        while True:
            name = f'value_{counter}'
            counter += 1
            if name not in reserved:
                reserved.add(name)
                return name

    def program(prog, outer):
        available = list(outer)
        steps = []

        def resolve(ref):
            if ref.startswith('input:'):
                name = ref[6:]
                if name not in params:
                    raise ValueError(f'unknown public input: {name}')
                return name
            index = int(ref[5:])
            if index >= len(available):
                raise ValueError(f'unknown or forward step reference: {ref}; available steps: 0..{len(available)-1}')
            return available[index]

        for step in prog['steps']:
            name = fresh()
            output = copy.deepcopy(step['output'])
            shapes[name] = output
            lowered = dict(kind=step['kind'], description=step['description'], produces={name: shape_type(output)})
            if step['kind'] == 'Compute':
                lowered['needs'] = [resolve(ref) for ref in step['needs']]
                if len(set(lowered['needs'])) != len(lowered['needs']):
                    raise ValueError('duplicate input reference')
                # Anonymous tuples at internal boundaries reintroduce ambiguous
                # field order. Public tuple outputs remain supported.
                for used in lowered['needs']:
                    if used in shapes and 'tuple' in shape_type(shapes[used]).lower():
                        raise ValueError('intermediate tuple values must use named record fields')
            else:
                lowered['condition'] = resolve(step['condition'])
                for arm in ('then', 'else'):
                    body = program(step[arm], available)
                    result = body['return']
                    result_shape = shapes.get(result, params.get(result))
                    if shape_structure(result_shape) != shape_structure(output):
                        raise ValueError(f'{arm} output structure differs from Branch output')
                    lowered[arm] = body
            steps.append(lowered)
            available.append(name)
        return {'steps': steps, 'return': resolve(prog['return'])}

    lowered = program(document, [])
    lowered.update(contract_version='1.0', interface=document['interface'])
    return lowered, shapes
