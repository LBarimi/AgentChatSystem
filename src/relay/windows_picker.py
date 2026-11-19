"""Native Windows folder selection using the Explorer file dialog."""
from threading import Lock
from uuid import UUID

from relay.errors import ApiError


_dialog_lock = Lock()
_CLSID_FILE_OPEN_DIALOG = 'DC1C5A9C-E88A-4DDE-A5A1-60F82A20AEF7'
_IID_FILE_OPEN_DIALOG = 'D57C7288-D4AD-4768-BE02-9D969532D960'
_IID_SHELL_ITEM = '43826D1E-E718-42EE-BC55-A1E261C37BFE'

# Method slots include the inherited IUnknown and IModalWindow methods.
_RELEASE = 2
_SHOW = 3
_SET_OPTIONS = 9
_GET_OPTIONS = 10
_SET_FOLDER = 12
_SET_TITLE = 17
_GET_RESULT = 20
_GET_DISPLAY_NAME = 5  # IShellItem


def choose_folder(initial, title):
    if not _dialog_lock.acquire(blocking=False):
        raise ApiError('A folder picker is already open.', 409, key='managedPickerBusy')
    try:
        return _show_dialog(initial, title)
    except OSError as error:
        raise ApiError('Could not open the system folder picker.', 503, key='managedPickerUnavailable') from error
    finally:
        _dialog_lock.release()


def _show_dialog(initial, title):
    # Import inside the Windows entry point so the server can start on Linux/macOS.
    import ctypes as c
    from ctypes import wintypes as w

    unknown = c.c_void_p
    guid = c.c_ubyte * 16
    ole = c.WinDLL('ole32')
    shell = c.WinDLL('shell32')

    ole.CoInitializeEx.argtypes = [unknown, w.DWORD]
    ole.CoCreateInstance.argtypes = [c.POINTER(guid), unknown, w.DWORD, c.POINTER(guid), c.POINTER(unknown)]
    ole.CoTaskMemFree.argtypes = [unknown]
    shell.SHCreateItemFromParsingName.argtypes = [w.LPCWSTR, unknown, c.POINTER(guid), c.POINTER(unknown)]

    def checked(result):
        if result < 0:
            raise OSError(f'Folder dialog failed: 0x{result & 0xffffffff:08x}')

    def call(obj, slot, result_type, arg_types=(), *values):
        table = c.cast(obj, c.POINTER(c.POINTER(unknown)))[0]
        method = c.WINFUNCTYPE(result_type, unknown, *arg_types)(table[slot])
        return method(obj, *values)

    def invoke(obj, slot, arg_types=(), *values):
        checked(call(obj, slot, c.c_long, arg_types, *values))

    dialog, start_folder, selected, display_path = (unknown() for _ in range(4))
    shell_item_id = guid.from_buffer_copy(UUID(_IID_SHELL_ITEM).bytes_le)
    checked(ole.CoInitializeEx(None, 2))  # COINIT_APARTMENTTHREADED
    try:
        dialog_class = guid.from_buffer_copy(UUID(_CLSID_FILE_OPEN_DIALOG).bytes_le)
        dialog_iid = guid.from_buffer_copy(UUID(_IID_FILE_OPEN_DIALOG).bytes_le)
        checked(ole.CoCreateInstance(c.byref(dialog_class), None, 1, c.byref(dialog_iid), c.byref(dialog)))
        options = w.DWORD()
        invoke(dialog, _GET_OPTIONS, (c.POINTER(w.DWORD),), c.byref(options))
        # Pick existing file-system folders only; keep the server's working directory.
        flags = 0x20 | 0x40 | 0x800 | 0x8  # FOS_PICKFOLDERS, FOS_FORCEFILESYSTEM, FOS_PATHMUSTEXIST, FOS_NOCHANGEDIR
        invoke(dialog, _SET_OPTIONS, (w.DWORD,), options.value | flags)
        invoke(dialog, _SET_TITLE, (w.LPCWSTR,), title)
        if shell.SHCreateItemFromParsingName(str(initial), None, c.byref(shell_item_id), c.byref(start_folder)) >= 0:
            invoke(dialog, _SET_FOLDER, (unknown,), start_folder)
        result = call(dialog, _SHOW, c.c_long, (w.HWND,), None)
        if result & 0xffffffff == 0x800704c7:  # HRESULT_FROM_WIN32(ERROR_CANCELLED)
            return None
        checked(result)
        invoke(dialog, _GET_RESULT, (c.POINTER(unknown),), c.byref(selected))
        invoke(selected, _GET_DISPLAY_NAME, (c.c_uint, c.POINTER(unknown)), 0x80058000, c.byref(display_path))
        return c.wstring_at(display_path.value)  # SIGDN_FILESYSPATH
    finally:
        if display_path:
            ole.CoTaskMemFree(display_path)
        for obj in (selected, start_folder, dialog):
            if obj:
                call(obj, _RELEASE, w.ULONG)
        ole.CoUninitialize()
