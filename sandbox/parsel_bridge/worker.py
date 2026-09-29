"""Run upstream parsing, SCC synthesis and serialization in the sandbox."""
import contextlib
import io
import json
import hashlib
import sys
from pathlib import Path
sys.path.insert(0, '/opt/parsel')
from codex import CodeGen
from consts import CONSTS
from graph import get_graph, get_root
import parsel

def main():
    request = json.loads(Path('/bridge/job.json').read_text())
    result = {'upstream_commit': request['upstream_commit']}
    log = io.StringIO()
    try:
        for name, digest in request['source_hashes'].items():
            if hashlib.sha256((Path('/opt/parsel') / name).read_bytes()).hexdigest() != digest:
                raise ValueError('container Parsel source differs from pinned checkout: ' + name)
        CONSTS['num_completions'] = 1
        CONSTS['min_completions'] = 1
        CONSTS['num_completions_eval'] = 1
        result['synthesis_settings'] = {
            'num_completions':1,'min_completions':1,'num_completions_eval':1,
            'max_tokens':CONSTS.get('max_tokens',500),'temperature':0.6,
            'generate_tests':False,'allow_autofill':False,'should_expand':False,
        }
        class QwenCodeGen(CodeGen):
            def generate(self, *args, **kwargs):
                kwargs['model_name'] = request['model']
                return super().generate(*args, **kwargs)
        Path('/tmp/key.txt').write_text('local:unused')
        with contextlib.redirect_stdout(log):
            generator = QwenCodeGen(cache='/tmp/cache.json', key='/tmp/key.txt')
            program = request['parsel'].splitlines(keepends=True)
            if '#*#*#\n' in program:
                index = program.index('#*#*#\n')
                header, program = program[:index], program[index + 1:]
            else:
                header = []
            _, functions = get_graph(program)
            for fn in functions.values(): fn.prefix = '\n'.join(header)
            result['parsel_valid'] = True
            result['function_prompts'] = {name: fn.get_codex_input() for name, fn in functions.items()}
            parsel.parsel_graph(functions, generator)
            root = get_root(functions)
            # Original writer's serializer; final benchmark artifact omits executed constraints.
            result['generated_artifact'] = CONSTS['exec_pre'] + parsel.fns_to_str(functions[root], set())
            result['constraints'] = {name: fn.get_assert_str() for name, fn in functions.items()}
            result['root_name'] = root
    except Exception as exc:
        result['synthesis_error'] = f'{type(exc).__name__}: {exc}'
    result['upstream_log'] = log.getvalue()[-20000:]
    temp = Path('/bridge/result.tmp')
    temp.write_text(json.dumps(result))
    temp.replace('/bridge/result.json')

if __name__ == '__main__': main()
