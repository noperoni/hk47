import requests, json
d = requests.get('https://api.pixellab.ai/v2/openapi.json', timeout=30).json()
comps = d.get('components', {}).get('schemas', {})

def walk(s, depth=0, seen=frozenset()):
    if not isinstance(s, dict):
        return str(s)
    if '$ref' in s:
        n = s['$ref'].split('/')[-1]
        if n in seen or depth > 3:
            return f'<{n}>'
        return walk(comps.get(n, {}), depth, seen | {n})
    if 'anyOf' in s:
        parts = [walk(x, depth, seen) for x in s['anyOf'] if x.get('type') != 'null']
        return parts[0] if len(parts) == 1 else parts
    if 'properties' in s or s.get('type') == 'object':
        req = set(s.get('required', []))
        return {k + ('*' if k in req else ''): (walk(v, depth+1, seen) if depth < 3 else (v.get('type') or 'obj'))
                for k, v in s.get('properties', {}).items()}
    if s.get('type') == 'array':
        return [walk(s.get('items', {}), depth+1, seen)]
    if 'enum' in s:
        return 'enum' + json.dumps(s['enum'])[:300]
    out = s.get('type', 'any')
    for k in ('default', 'minimum', 'maximum', 'description'):
        if k in s:
            out += f' ({k}={str(s[k])[:90]})'
    return out

import sys
for p in sys.argv[1:]:
    op = d['paths'][p]['post']
    body = op.get('requestBody', {}).get('content', {}).get('application/json', {}).get('schema', {})
    print('=' * 72)
    print('POST', p, '|', op.get('summary', ''))
    print(json.dumps(walk(body), indent=2)[:3000])
