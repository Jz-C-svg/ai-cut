"""Isolated, read-only local Jianying draft decoder. Never attach to an editor.

MSVC string ABI/export reference: wenshui330/jy-draftc @
130aa03ab9e932d0f7062eec4b95d046a5985b97 (MIT). See external/re-tools/NOTICE.md.
Only the read function is exposed. Allocations owned by the DLL end with this
short-lived child process; no cross-runtime free or DLL unload is attempted.
"""
from __future__ import annotations

import ctypes as ct
import hashlib
import json
import os
import struct
import sys
from pathlib import Path

DEC_EXPORT = ('?decrypt@EncryptUtils@lvve@@QEAA?AV?$basic_string@DU?$char_traits@D@std@@'
              'V?$allocator@D@2@@std@@AEBV34@0AEA_N@Z')
LIMIT = 64 * 1024 * 1024


class StringData(ct.Union):
    _fields_ = [('small', ct.c_char * 16), ('ptr', ct.c_void_p)]


class MsvcString(ct.Structure):
    _fields_ = [('data', StringData), ('size', ct.c_uint64), ('capacity', ct.c_uint64)]


def argument(data):
    backing = ct.create_string_buffer(data)
    value = MsvcString(size=len(data), capacity=max(15, len(data)))
    if len(data) < 16:
        ct.memmove(ct.addressof(value), backing, len(data))
    else:
        value.data.ptr = ct.addressof(backing)
    return value, backing


def run(request):
    if os.name != 'nt' or struct.calcsize('P') != 8:
        raise ValueError('Windows x64 is required')
    dll = Path(request['dll']).resolve(strict=True)
    source = Path(request['source']).resolve(strict=True)
    output = Path(request['output']).resolve()
    if dll.name.lower() != 'videoeditor.dll' or source.name not in ('draft_content.json', 'draft_meta_info.json'):
        raise ValueError('Only local draft JSON input and videoeditor.dll are supported')
    if output == source or output.exists():
        raise ValueError('Output must be a new independent file')
    if source.stat().st_size > LIMIT:
        raise ValueError('Draft exceeds decoder limit')
    with dll.open('rb') as stream:
        dll_hash = hashlib.file_digest(stream, 'sha256').hexdigest()
    if dll_hash != request['dll_sha256']:
        raise ValueError('Pinned DLL changed')
    data = source.read_bytes()
    if hashlib.sha256(data).hexdigest() != request['source_sha256']:
        raise ValueError('Input snapshot changed')
    # Search state is changed only in this disposable process. Never use the
    # working directory or PATH to select the top-level DLL.
    os.environ['PATH'] = str(dll.parent) + os.pathsep + os.environ.get('PATH', '')
    ct.windll.kernel32.SetErrorMode(0x0001 | 0x8000)
    with os.add_dll_directory(str(dll.parent)):
        library = ct.WinDLL(str(dll), winmode=0x100 | 0x800 | 0x1000 | 0x400)
        fn = getattr(library, DEC_EXPORT)
        fn.argtypes = [ct.c_void_p, ct.POINTER(MsvcString), ct.POINTER(MsvcString),
                       ct.POINTER(MsvcString), ct.POINTER(ct.c_bool)]
        fn.restype = ct.POINTER(MsvcString)
        incoming, keep_in = argument(data)
        params, keep_params = argument(b'{}')
        outgoing, ok = MsvcString(capacity=15), ct.c_bool(False)
        fn(None, ct.byref(outgoing), ct.byref(incoming), ct.byref(params), ct.byref(ok))
        if not ok.value or not 0 < outgoing.size <= LIMIT or outgoing.capacity < outgoing.size:
            raise ValueError('Native decoder returned an invalid result')
        ptr = ct.addressof(outgoing) if outgoing.capacity < 16 else outgoing.data.ptr
        if not ptr:
            raise ValueError('Native decoder returned an empty buffer')
        plain = ct.string_at(ptr, outgoing.size)
        parsed = json.loads(plain.decode('utf-8-sig'))
        if not isinstance(parsed, dict):
            raise ValueError('Decoded data is not a JSON object')
        if hashlib.sha256(source.read_bytes()).hexdigest() != request['source_sha256']:
            raise ValueError('Input snapshot changed during decoding')
        with output.open('xb') as stream:
            stream.write(plain)
        return {'ok': True, 'bytes': len(plain), 'sha256': hashlib.sha256(plain).hexdigest(),
                'source_sha256': request['source_sha256'], 'dll_sha256': request['dll_sha256']}


if __name__ == '__main__':
    try:
        result = run(json.loads(sys.stdin.read()))
    except Exception as exc:
        # Do not emit source contents or native exception output.
        result = {'ok': False, 'error': type(exc).__name__}
    # Native libraries can print initialization messages; transport the result
    # through a separate file instead of attempting to parse their stdout.
    with Path(sys.argv[1]).open('x', encoding='utf-8') as stream:
        json.dump(result, stream)
    # DLL-owned C++ allocations are intentionally reclaimed by process exit.
    os._exit(0 if result['ok'] else 1)
