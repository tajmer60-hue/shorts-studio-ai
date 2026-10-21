"""Small real CUDA/cuBLAS operation; no model downloads and no system changes."""
import ctypes as C
import json
from pathlib import Path
import sys
from cuda_runtime import configure_cuda


def verify():
    directories = configure_cuda()

    def library(filename):
        for directory in directories:
            candidate = Path(directory) / filename
            if candidate.is_file():
                return C.WinDLL(str(candidate))
        raise RuntimeError('Не найдена локальная библиотека: ' + filename)

    def function(dll, name, args):
        func = getattr(dll, name)
        func.argtypes, func.restype = args, C.c_int
        return func

    def checked(func, *args):
        status = func(*args)
        if status != 0:
            raise RuntimeError(f'{func.__name__}: status {status}')

    cuda = library('cudart64_12.dll')
    blas = library('cublas64_12.dll')
    cudnn = library('cudnn64_9.dll')
    pointer = C.c_void_p
    malloc = function(cuda, 'cudaMalloc', [C.POINTER(pointer), C.c_size_t])
    free = function(cuda, 'cudaFree', [pointer])
    copy = function(cuda, 'cudaMemcpy', [pointer, pointer, C.c_size_t, C.c_int])
    create = function(blas, 'cublasCreate_v2', [C.POINTER(pointer)])
    destroy = function(blas, 'cublasDestroy_v2', [pointer])
    multiply = function(blas, 'cublasSgemm_v2', [pointer, C.c_int, C.c_int,
        C.c_int, C.c_int, C.c_int, C.POINTER(C.c_float), pointer, C.c_int,
        pointer, C.c_int, C.POINTER(C.c_float), pointer, C.c_int])
    handle = pointer()
    buffers = []
    try:
        checked(create, C.byref(handle))
        for _ in range(3):
            device = pointer()
            checked(malloc, C.byref(device), 16)
            buffers.append(device)
        left = (C.c_float * 4)(1, 2, 3, 4)
        identity = (C.c_float * 4)(1, 0, 0, 1)
        output = (C.c_float * 4)()
        checked(copy, buffers[0], C.cast(left, pointer), 16, 1)
        checked(copy, buffers[1], C.cast(identity, pointer), 16, 1)
        alpha, beta = C.c_float(2), C.c_float(0)
        checked(multiply, handle, 0, 0, 2, 2, 2, C.byref(alpha), buffers[0], 2,
                buffers[1], 2, C.byref(beta), buffers[2], 2)
        checked(copy, C.cast(output, pointer), buffers[2], 16, 2)
        if list(output) != [2, 4, 6, 8]:
            raise RuntimeError('Неверный результат GPU-вычисления: ' + str(list(output)))
        neural = pointer()
        neural_create = function(cudnn, 'cudnnCreate', [C.POINTER(pointer)])
        neural_destroy = function(cudnn, 'cudnnDestroy', [pointer])
        checked(neural_create, C.byref(neural))
        checked(neural_destroy, neural)
        return dict(ok=True, matrix_result=list(output), cudnn_handle='created and destroyed',
            note='Real GPU matrix multiplication and cuDNN initialization; full Whisper inference not tested')
    finally:
        for device in buffers:
            free(device)
        if handle.value:
            destroy(handle)


if __name__ == '__main__':
    try:
        result = verify()
    except Exception as error:
        result = dict(ok=False, error=str(error))
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if len(sys.argv) > 1:
        Path(sys.argv[1]).write_text(text, encoding='utf-8')
    print(text)
    sys.exit(0 if result['ok'] else 1)
