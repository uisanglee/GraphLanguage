"""Legacy OpenAI transport shim. Original Parsel CodeGen stays unmodified."""
import json
import time
import uuid
from pathlib import Path
organization = None
api_key = None

class error:
    class RateLimitError(Exception):
        pass

class Choice(dict):
    @property
    def text(self):
        return self['text']

class Completion:
    @staticmethod
    def create(**payload):
        key = uuid.uuid4().hex
        path = Path('/bridge') / (key + '.request.json')
        temp = path.with_suffix('.tmp')
        temp.write_text(json.dumps(payload))
        temp.replace(path)
        response = Path('/bridge') / (key + '.response.json')
        started = time.monotonic()
        while not response.exists():
            if time.monotonic() - started > 900:
                raise TimeoutError('Qwen transport timed out; no resampling')
            time.sleep(0.1)
        value = json.loads(response.read_text())
        if 'error' in value:
            raise RuntimeError(value['error'])
        value['choices'] = [Choice(choice) for choice in value['choices']]
        return value
