"""Deterministic stdio MCP server for reload acceptance, without dependencies."""
import json
import os
import sys

for line in sys.stdin:
    message = json.loads(line)
    method = message.get('method')
    if 'id' not in message:
        continue
    if method == 'initialize':
        result = {
            'protocolVersion': message['params']['protocolVersion'],
            'capabilities': {'tools': {}},
            'serverInfo': {'name': 'reload-test', 'version': '1'},
        }
    elif method == 'tools/list':
        result = {'tools': [{
            'name': 'echo', 'description': 'Return the synthetic profile marker and value.',
            'inputSchema': {'type': 'object', 'properties': {'value': {'type': 'string'}}, 'required': ['value']},
        }]}
    elif method == 'tools/call':
        value = message['params']['arguments']['value']
        call_log = os.environ.get('RELOAD_CALL_LOG')
        if call_log:
            with open(call_log, 'a', encoding='utf-8') as log:
                log.write(json.dumps(message['params']) + '\n')
        result = {'content': [{'type': 'text', 'text': f"{os.environ.get('RELOAD_MARKER', 'missing')}:{value}"}]}
    elif method == 'ping':
        result = {}
    else:
        print(json.dumps({'jsonrpc': '2.0', 'id': message['id'], 'error': {'code': -32601, 'message': method}}), flush=True)
        continue
    print(json.dumps({'jsonrpc': '2.0', 'id': message['id'], 'result': result}), flush=True)
