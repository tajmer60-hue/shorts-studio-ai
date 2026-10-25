"""Windows current-user encrypted credential storage. No network calls."""
import ctypes
from ctypes import wintypes
import os
from pathlib import Path


def protect(value,decrypt=False):
    if os.name!='nt':raise ValueError('Защищённое хранение доступно только в Windows.')
    class Blob(ctypes.Structure):
        _fields_=[('size',wintypes.DWORD),('data',ctypes.POINTER(ctypes.c_ubyte))]
    buffer=ctypes.create_string_buffer(value);source=Blob(len(value),ctypes.cast(buffer,ctypes.POINTER(ctypes.c_ubyte)));out=Blob()
    crypt=ctypes.WinDLL('crypt32',use_last_error=True);kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    fn=crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    fn.argtypes=[ctypes.POINTER(Blob),ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(Blob)]
    fn.restype=wintypes.BOOL;kernel.LocalFree.argtypes=[ctypes.c_void_p];kernel.LocalFree.restype=ctypes.c_void_p
    if not fn(ctypes.byref(source),None,None,None,None,1,ctypes.byref(out)):
        raise ValueError('Windows не смогла защитить или прочитать ключ.')
    try:return ctypes.string_at(out.data,out.size)
    finally:kernel.LocalFree(out.data)


def save_key(path,key):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix('.tmp');temporary.write_bytes(protect(key.encode()))
    temporary.replace(path)


def load_key(path):
    try:return protect(Path(path).read_bytes(),decrypt=True).decode()
    except FileNotFoundError:return ''
