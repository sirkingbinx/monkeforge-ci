import glob
import json
import os
from pathlib import Path
import re
import secrets
import sys
import urllib.error
import urllib.parse
import urllib.request


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

def publish(env):
    # Deliberately fixed: neither action inputs nor environment can redirect keys.
    origin = 'https://monkeforge.org'
    token = env.get('MF_API_KEY', '').strip()
    if not token or any(c.isspace() for c in token): raise ValueError('api-key is required and must not contain whitespace.')
    mod_id = env.get('MF_MOD_ID', '').strip()
    if not mod_id or len(mod_id) > 200: raise ValueError('mod-id is required.')
    version = env.get('MF_VERSION', '').strip()
    if not re.fullmatch(r'[0-9][a-zA-Z0-9.+_-]{0,63}', version): raise ValueError('version must start with a digit and be at most 64 characters.')
    channel = env.get('MF_CHANNEL', 'stable').strip()
    if not re.fullmatch('[a-z][a-z0-9-]{0,39}', channel): raise ValueError('Invalid channel name.')
    notes = env.get('MF_NOTES', '')
    if len(notes) > 10000: raise ValueError('notes must be at most 10,000 characters.')
    files = []
    for pattern in env.get('MF_FILES', '').splitlines():
        if not pattern.strip(): continue
        matched = sorted(glob.glob(pattern.strip(), recursive=True))
        if not matched: raise ValueError('A files pattern matched no build files.')
        for path in matched:
            file = Path(path).resolve()
            if not file.is_file(): raise ValueError('Each matched path must be a file.')
            if file not in files: files.append(file)
    formats = [file.suffix.lower() for file in files]
    if not 1 <= len(files) <= 2 or any(f not in ('.dll','.zip') for f in formats) or len(set(formats)) != len(formats):
        raise ValueError('files must match one DLL, one ZIP, or one of each.')
    if any(file.stat().st_size > 50*1024*1024 for file in files): raise ValueError('Each build file must be at most 50 MB.')
    boundary = 'MonkeForge' + secrets.token_hex(24)
    parts = []
    for name, value in [('version',version),('channel',channel),('notes',notes)]:
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'.encode() + value.encode() + b'\r\n')
    for file in files:
        filename = re.sub(r'[^a-zA-Z0-9._ -]', '_', file.name)
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="downloads"; filename="{filename}"\r\nContent-Type: application/octet-stream\r\n\r\n'.encode() + file.read_bytes() + b'\r\n')
    parts.append(f'--{boundary}--\r\n'.encode())
    endpoint = origin + '/api/v1/mods/' + urllib.parse.quote(mod_id, safe='') + '/releases'
    request = urllib.request.Request(endpoint, data=b''.join(parts), headers={
        'Authorization': 'Bearer ' + token, 'Content-Type': 'multipart/form-data; boundary=' + boundary,
        'User-Agent': 'MonkeForge-GitHub-Action/1.0'})
    try:
        with urllib.request.build_opener(NoRedirect).open(request, timeout=120) as result:
            payload = json.load(result)
    except urllib.error.HTTPError as exc:
        # Never echo server response text: it may contain reflected credentials.
        exc.close()
        raise ValueError(f'MonkeForge rejected the upload (HTTP {exc.code}). Check the key, mod ownership, channel, version, and file validity.') from None
    except urllib.error.URLError:
        raise ValueError('Could not reach MonkeForge. Check runner network access to https://monkeforge.org.') from None
    if not isinstance(payload, dict) or not isinstance(payload.get('guid'), str) or not isinstance(payload.get('url'), str):
        raise ValueError('MonkeForge returned an invalid release response.')
    path = urllib.parse.urlsplit(payload['url'])
    if path.scheme or path.netloc or not path.path.startswith('/view/') or any(c in payload['url'] for c in '\r\n'):
        raise ValueError('MonkeForge returned an invalid release URL.')
    release_url = origin + payload['url']
    if env.get('GITHUB_OUTPUT'):
        # Reject newlines to prevent server responses from injecting other outputs.
        guid = payload['guid']
        if any(c in guid + release_url for c in '\r\n'): raise ValueError('Invalid release output.')
        with open(env['GITHUB_OUTPUT'], 'a') as output:
            output.write(f'mod-guid={guid}\nrelease-url={release_url}\n')
    return payload


if __name__ == '__main__':
    try:
        publish(os.environ)
    except (ValueError, OSError) as exc:
        print(f'Upload failed: {exc}', file=sys.stderr)
        sys.exit(1)
    print('Release uploaded to MonkeForge and submitted for review.')
