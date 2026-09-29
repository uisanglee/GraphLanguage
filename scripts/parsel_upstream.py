"""Sandboxed upstream Parsel with an external, cached Qwen completion transport."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import urllib.request
import uuid
ROOT = Path(__file__).resolve().parents[1]
COMMIT = 'bc1687cab9e8a79249f0d3e3cb4f5826dba39a95'

def verify_upstream():
    repo = ROOT / 'third_party/parsel'
    head = subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD'], text=True).strip()
    dirty = subprocess.check_output(['git', '-C', str(repo), 'status', '--porcelain', '--untracked-files=no'], text=True)
    if head != COMMIT or dirty:
        raise ValueError('Parsel must match the clean pinned upstream commit')
    names = subprocess.check_output(['git','-C',str(repo),'ls-files','*.py'],text=True).splitlines()
    return {name:hashlib.sha256((repo/name).read_bytes()).hexdigest() for name in names}

def run_upstream(parsel_text, model, base_url, api_key, cache_dir, image='graphdsl-parsel:0.1', timeout=1800):
    source_hashes = verify_upstream()
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    events = []
    image_info = subprocess.check_output(['docker','image','inspect',image,'--format','{{.Id}}'], text=True).strip()
    name = 'parsel-' + uuid.uuid4().hex[:12]
    with tempfile.TemporaryDirectory(prefix='parsel-bridge-') as tempdir:
        bridge = Path(tempdir)
        os.chmod(bridge, 0o777)
        (bridge / 'job.json').write_text(json.dumps(dict(parsel=parsel_text, model=model, upstream_commit=COMMIT, source_hashes=source_hashes)))
        command = ['docker','run','--rm','--name',name,'--network','none','--read-only',
                   '--cap-drop','ALL','--security-opt','no-new-privileges','--pids-limit','128',
                   '--memory','2g','--memory-swap','2g','--cpus','2',
                   '--tmpfs','/tmp:rw,nosuid,nodev,size=256m','--volume',f'{bridge}:/bridge:rw',image]
        with tempfile.TemporaryFile() as logs:
            process = subprocess.Popen(command, stdout=logs, stderr=logs)
            started = time.monotonic()
            try:
                while process.poll() is None:
                    if time.monotonic() - started > timeout:
                        raise TimeoutError('upstream Parsel sandbox deadline exceeded')
                    for request in sorted(bridge.glob('*.request.json')):
                        response = request.with_name(request.name.replace('.request.json','.response.json'))
                        if response.exists(): continue
                        payload = json.loads(request.read_text())
                        if payload.get('n') != 1 or payload.get('model') != model:
                            raise ValueError('original requested a budget outside the 1x1 protocol')
                        key = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
                        cached = cache_dir / (key + '.json')
                        if cached.exists():
                            saved = json.loads(cached.read_text())
                            if saved.get('status') != 'completed':
                                raise RuntimeError(f'uncertain completion; reconcile {cached}')
                            data = saved['response']
                        else:
                            cached.write_text(json.dumps({'status':'pending'}))
                            headers = {'Content-Type':'application/json'}
                            if api_key: headers['Authorization'] = 'Bearer ' + api_key
                            req = urllib.request.Request(base_url.rstrip('/') + '/completions',
                                data=json.dumps(payload).encode(), headers=headers)
                            request_started = time.monotonic()
                            with urllib.request.urlopen(req, timeout=600) as stream: data = json.load(stream)
                            data['_elapsed_seconds'] = time.monotonic() - request_started
                            temp = cached.with_suffix('.tmp')
                            temp.write_text(json.dumps({'status':'completed','response':data}))
                            temp.replace(cached)
                        events.append({'usage':data.get('usage',{}), 'prompt_sha256':key,
                                       'elapsed_seconds':data.get('_elapsed_seconds',0)})
                        temp = response.with_suffix('.tmp')
                        temp.write_text(json.dumps(data))
                        temp.replace(response)
                    time.sleep(0.1)
                result_path = bridge / 'result.json'
                if not result_path.exists():
                    logs.seek(0)
                    raise RuntimeError('Parsel sandbox failed: ' + logs.read()[-4000:].decode(errors='replace'))
                result = json.loads(result_path.read_text())
                result.update(function_inference=events, upstream_image_id=image_info)
                return result
            finally:
                if process.poll() is None:
                    subprocess.run(['docker','rm','-f',name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    process.wait(timeout=15)
