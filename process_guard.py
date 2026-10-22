"""Windows job object: terminate this export's FFmpeg if the app crashes."""
import os


class ProcessGuard:
    def __init__(self, process):
        self.handle = None
        if os.name != 'nt':
            return
        import ctypes as c
        from ctypes import wintypes as w

        class Basic(c.Structure):
            _fields_ = [('process_time', c.c_int64), ('job_time', c.c_int64),
                        ('flags', w.DWORD), ('min_working_set', c.c_size_t),
                        ('max_working_set', c.c_size_t), ('active_processes', w.DWORD),
                        ('affinity', c.c_size_t), ('priority', w.DWORD), ('scheduling', w.DWORD)]

        class IO(c.Structure):
            _fields_ = [(name, c.c_uint64) for name in ('read_ops','write_ops','other_ops','read_bytes','write_bytes','other_bytes')]

        class Limits(c.Structure):
            _fields_ = [('basic', Basic), ('io', IO), ('process_memory', c.c_size_t),
                        ('job_memory', c.c_size_t), ('peak_process_memory', c.c_size_t),
                        ('peak_job_memory', c.c_size_t)]

        api = c.WinDLL('kernel32', use_last_error=True)
        api.CreateJobObjectW.argtypes = [c.c_void_p, w.LPCWSTR]
        api.CreateJobObjectW.restype = w.HANDLE
        api.SetInformationJobObject.argtypes = [w.HANDLE, c.c_int, c.c_void_p, w.DWORD]
        api.SetInformationJobObject.restype = w.BOOL
        api.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
        api.OpenProcess.restype = w.HANDLE
        api.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
        api.AssignProcessToJobObject.restype = w.BOOL
        api.CloseHandle.argtypes = [w.HANDLE]
        api.CloseHandle.restype = w.BOOL
        self.api = api
        handle = api.CreateJobObjectW(None, None)
        if not handle:
            raise c.WinError(c.get_last_error())
        child = None
        try:
            limits = Limits()
            limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not api.SetInformationJobObject(handle, 9, c.byref(limits), c.sizeof(limits)):
                raise c.WinError(c.get_last_error())
            child = api.OpenProcess(0x101, False, process.pid)
            if not child or not api.AssignProcessToJobObject(handle, child):
                raise c.WinError(c.get_last_error())
            self.handle = handle
        except BaseException:
            api.CloseHandle(handle)
            raise
        finally:
            if child:
                api.CloseHandle(child)

    def close(self):
        if self.handle:
            self.api.CloseHandle(self.handle)
            self.handle = None
