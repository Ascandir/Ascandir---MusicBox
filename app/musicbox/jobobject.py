"""Windows-„Job“: Alles, was MusicBox startet (ComfyUI, Strata, 7-Zip …), hängt an diesem Python-Prozess.

Endet MusicBox – egal ob normal, per Task-Manager oder durch einen Absturz – beendet Windows automatisch alle
Unterprozesse mit. So bleibt nie ein Python oder eine KI im Hintergrund hängen und belegt Grafikspeicher.
Nur das Update-Setup darf sich lösen (CREATE_BREAKAWAY_FROM_JOB), damit es nach dem Beenden weiterläuft.
"""
import sys

_handle = None
BREAKAWAY = 0x01000000          # CREATE_BREAKAWAY_FROM_JOB – für Prozesse, die weiterleben sollen


def enable():
    global _handle
    if sys.platform != "win32" or _handle is not None:
        return False
    import ctypes
    from ctypes import wintypes

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [(n, ctypes.c_ulonglong) for n in ("ReadOperationCount", "WriteOperationCount",
                                                      "OtherOperationCount", "ReadTransferCount",
                                                      "WriteTransferCount", "OtherTransferCount")]

    class BASIC(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class EXTENDED(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", BASIC), ("IoInfo", IO_COUNTERS),
                    ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateJobObjectW.restype = wintypes.HANDLE
    k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    k32.GetCurrentProcess.restype = wintypes.HANDLE
    k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]

    job = k32.CreateJobObjectW(None, None)
    if not job:
        return False
    info = EXTENDED()
    # KILL_ON_JOB_CLOSE (0x2000) | BREAKAWAY_OK (0x800)
    info.BasicLimitInformation.LimitFlags = 0x2000 | 0x800
    if not k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
        return False
    if not k32.AssignProcessToJobObject(job, k32.GetCurrentProcess()):
        return False
    _handle = job          # nie schließen: Windows schließt ihn, wenn Python endet → alles wird beendet
    return True
