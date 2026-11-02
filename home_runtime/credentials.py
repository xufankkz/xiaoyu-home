"""Windows DPAPI secrets, bound to the current Windows account."""
from pathlib import Path
import ctypes
from ctypes import wintypes
import json
import os
import threading
from .files import atomic_bytes

class Blob(ctypes.Structure):
    _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_byte))]

def protect(raw, decrypt=False):
    if os.name != 'nt':
        raise RuntimeError('此测试发行版的凭据保护需要 Windows。')
    buffer = ctypes.create_string_buffer(raw)
    source = Blob(len(raw), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
    output = Blob()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    function = crypt32.CryptUnprotectData if decrypt else crypt32.CryptProtectData
    function.argtypes = (ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                         ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob))
    function.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = (ctypes.c_void_p,)
    kernel32.LocalFree.restype = ctypes.c_void_p
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output)):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(output.data, output.size)
    finally:
        kernel32.LocalFree(output.data)

class Credentials:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.RLock()
    def values(self):
        if not self.path.exists():
            return {}
        try:
            return json.loads(protect(self.path.read_bytes(), decrypt=True))
        except Exception:
            raise ValueError('本机加密凭据无法读取，请在当前 Windows 账号下重新填写。') from None
    def get(self, name):
        with self.lock:
            return self.values().get(name, '')
    def set(self, name, value):
        if not isinstance(value, str) or len(value) > 4096 or '\n' in value or '\r' in value:
            raise ValueError('凭据格式无效')
        with self.lock:
            values = self.values()
            if value:
                values[name] = value
            else:
                values.pop(name, None)
            atomic_bytes(self.path, protect(json.dumps(values, ensure_ascii=False).encode()))
