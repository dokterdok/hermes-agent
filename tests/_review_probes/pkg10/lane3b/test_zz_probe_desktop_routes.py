"""Execute real Desktop routing exports with Node's TS loader (no source-text assertions)."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.platforms('windows')
@pytest.mark.parametrize('name', ['active', 'import', 'sessions', 'beta'])
def test_named_profile_delete_uses_its_own_registry_grant(tmp_path, name):
    node = shutil.which('node') or pytest.skip('node is not on PATH')
    (tmp_path / 'profiles' / name).mkdir(parents=True)
    script = r'''
import { registerHooks } from 'node:module';
import { existsSync } from 'node:fs';
import { pathToFileURL } from 'node:url';
registerHooks({resolve(specifier, context, nextResolve) {
  if (specifier.startsWith('.') && context.parentURL) {
    const candidate = new URL(specifier + '.ts', context.parentURL);
    if (existsSync(candidate)) return nextResolve(candidate.href, context);
  }
  return nextResolve(specifier, context);
}});
const local = await import(pathToFileURL(process.cwd() + '/apps/desktop/electron/local-gateway.ts'));
const config = await import(pathToFileURL(process.cwd() + '/apps/desktop/electron/connection-config.ts'));
const [home, name] = process.argv.slice(1);
let recorded;
local.configurePythonGatewayTicketClient(async (endpoint, purpose) => {
  recorded = {profile: endpoint.profile_id, purpose}; return 'probe';
});
const route = config.pathForRegistryBackendRequest('/api/profiles/' + name, 'default', {mode: 'local'});
const origin = 'http://127.0.0.1:8000';
const endpoint = {profile_id: home, api_origin: origin, instance_id: 'review'};
await local.nativeGatewayHttpHeaders({gatewayEndpoint: endpoint, baseUrl: origin}, origin + route, home, 'DELETE');
console.log(JSON.stringify({route, ...recorded}));
'''
    result = subprocess.run([node, '--input-type=module', '--eval', script, str(tmp_path), name],
                            cwd=Path(__file__).resolve().parents[3], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    answer = json.loads(result.stdout)
    assert answer == {'route': f'/api/profiles/{name}', 'profile': str(tmp_path / 'profiles' / name),
                      'purpose': 'native-profile-registry'}
