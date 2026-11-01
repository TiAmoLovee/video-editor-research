"""Explicit ASR execution settings; GPU use never silently falls back to CPU."""
import os


def runtime_settings(*, device=None, compute_type=None):
    device = device if device is not None else os.environ.get('CLIPFORGE_ASR_DEVICE') or 'cpu'
    if device not in ('cpu', 'cuda'):
        raise ValueError('CLIPFORGE_ASR_DEVICE 必须是 cpu 或 cuda')
    compute_type = (compute_type if compute_type is not None else
                    os.environ.get('CLIPFORGE_ASR_COMPUTE_TYPE') or
                    ('int8' if device == 'cpu' else 'float16'))
    allowed = ('int8', 'float32') if device == 'cpu' else ('float16', 'int8_float16')
    if compute_type not in allowed:
        raise ValueError('CLIPFORGE_ASR_COMPUTE_TYPE 与所选 device 不兼容')
    return {'device': device, 'compute_type': compute_type, 'cpu_threads': 4, 'num_workers': 1}


def validate_cached_runtime(parameters, expected):
    """Bind restored ASR metadata to the requested execution settings."""
    if (not isinstance(expected, dict)
            or set(expected) != {'device', 'compute_type', 'cpu_threads', 'num_workers'}
            or expected != runtime_settings(device=expected.get('device'), compute_type=expected.get('compute_type'))
            or not isinstance(parameters, dict)
            or any(type(parameters.get(key)) is not type(value) or parameters.get(key) != value
                   for key, value in expected.items())):
        raise ValueError('缓存 ASR device 或 compute_type 与请求不匹配')
