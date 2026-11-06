"""Invoke a file's default Explorer action without choosing a registry verb."""
import ctypes as C
from ctypes import wintypes as W
from pathlib import Path
import uuid

_GUID = C.c_ubyte * 16

def guid(value):
    return _GUID.from_buffer_copy(uuid.UUID(value).bytes_le)


class Record(C.Structure):
    _fields_ = [('record', C.c_void_p), ('info', C.c_void_p)]


class Value(C.Union):
    _fields_ = [('pointer', C.c_void_p), ('integer', C.c_longlong), ('record', Record)]


class Variant(C.Structure):
    _anonymous_ = ('value',)
    _fields_ = [('vt', W.USHORT), ('r1', W.USHORT), ('r2', W.USHORT), ('r3', W.USHORT), ('value', Value)]


class Parameters(C.Structure):
    _fields_ = [('args', C.POINTER(Variant)), ('named', C.POINTER(W.LONG)), ('count', W.UINT), ('named_count', W.UINT)]


class ExceptionInfo(C.Structure):
    _fields_ = [('code', W.WORD), ('reserved', W.WORD), ('source', C.c_void_p), ('description', C.c_void_p), ('help', C.c_void_p), ('context', W.DWORD), ('pv', C.c_void_p), ('fill', C.c_void_p), ('scode', W.LONG)]


def method(pointer, index, result, *arguments):
    table = C.cast(pointer, C.POINTER(C.POINTER(C.c_void_p))).contents
    return C.WINFUNCTYPE(result, C.c_void_p, *arguments)(table[index])


def invoke_default(path):
    """Caller owns the initialized STA. Every BSTR, VARIANT and COM ref is released."""
    ole = C.WinDLL('ole32')
    automation = C.WinDLL('oleaut32')
    ole.CoCreateInstance.argtypes = [C.c_void_p, C.c_void_p, W.DWORD, C.c_void_p, C.POINTER(C.c_void_p)]
    ole.CoCreateInstance.restype = W.LONG
    automation.SysAllocStringLen.argtypes = [W.LPCWSTR, W.UINT]
    automation.SysAllocStringLen.restype = C.c_void_p
    automation.SysFreeString.argtypes = [C.c_void_p]
    automation.VariantClear.argtypes = [C.POINTER(Variant)]
    null = guid('00000000-0000-0000-0000-000000000000')

    def call(pointer, name, text=None):
        names = (W.LPCWSTR * 1)(name)
        identifier = W.LONG()
        result = method(pointer, 5, W.LONG, C.c_void_p, C.POINTER(W.LPCWSTR), W.UINT, W.DWORD, C.POINTER(W.LONG))(pointer, null, names, 1, 0, C.byref(identifier))
        if result < 0:
            raise OSError(f'No se encontró la acción del Explorador ({result & 0xffffffff:08X}).')
        argument = Variant()
        output = Variant()
        exception = ExceptionInfo()
        parameters = Parameters()
        try:
            if text is not None:
                argument.vt = 8  # VT_BSTR; Unicode path stays a literal value.
                argument.pointer = automation.SysAllocStringLen(text, len(text.encode('utf-16-le')) // 2)
                if not argument.pointer:
                    raise MemoryError('No se pudo preparar la ruta.')
                parameters.args = C.pointer(argument)
                parameters.count = 1
            result = method(pointer, 6, W.LONG, W.LONG, C.c_void_p, W.DWORD, W.WORD, C.POINTER(Parameters), C.POINTER(Variant), C.POINTER(ExceptionInfo), C.c_void_p)(pointer, identifier, null, 0, 1, C.byref(parameters), C.byref(output), C.byref(exception), None)
            if result < 0:
                automation.VariantClear(C.byref(output))
                raise OSError(f'El Explorador no pudo abrir el archivo ({result & 0xffffffff:08X}).')
            return output
        finally:
            automation.VariantClear(C.byref(argument))
            for string in (exception.source, exception.description, exception.help):
                if string:
                    automation.SysFreeString(string)

    shell = C.c_void_p()
    outputs = []
    try:
        result = ole.CoCreateInstance(guid('13709620-c279-11ce-a49e-444553540000'), None, 1, guid('00020400-0000-0000-c000-000000000046'), C.byref(shell))
        if result < 0:
            raise OSError(f'No se pudo conectar con el Explorador ({result & 0xffffffff:08X}).')
        target = Path(path)
        folder = call(shell, 'NameSpace', str(target.parent));outputs.append(folder)
        if folder.vt != 9 or not folder.pointer:
            raise FileNotFoundError('La carpeta del archivo no está disponible.')
        item = call(folder.pointer, 'ParseName', target.name);outputs.append(item)
        if item.vt != 9 or not item.pointer:
            raise FileNotFoundError('El archivo ya no está disponible.')
        output = call(item.pointer, 'InvokeVerb');outputs.append(output)
        return 0
    finally:
        for value in reversed(outputs):
            automation.VariantClear(C.byref(value))
        if shell:
            method(shell, 2, W.ULONG)(shell)

