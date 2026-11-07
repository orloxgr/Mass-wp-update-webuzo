#!/usr/bin/python3
import json
import os
from pathlib import Path
import sys

sys.dont_write_bytecode = True
sys.pycache_prefix = str(Path(__file__).resolve().parent / '.bytecode')
sys.path.insert(0, str(Path(__file__).resolve().parent))
import workers
with workers.state.locked():
    generation=workers.pin()
sys.path.insert(0,str(generation))
del sys.modules['workers']
import engine
import state
import transport

if __name__ == '__main__':
    validated = False
    try:
        if os.geteuid() != 0 or len(sys.argv) != 3 or sys.argv[1] != '--request': raise ValueError('Use the native Webuzo panel')
        request = transport.request(sys.argv[2])
        validated = True
        with state.locked(): result = engine.dispatch(request)
    except Exception as error:
        result = dict(ok=False, error=str(error))
    if validated:
        transport.publish(sys.argv[2],json.dumps(result).encode())
        print(json.dumps(dict(ok=True,response=sys.argv[2])))
    else:
        print(json.dumps(result)); raise SystemExit(1)
