def normalize_config(config):
    allowed = {'timeout'}
    unknown = set(config) - allowed
    if unknown:
        raise ValueError(f'unknown config keys: {sorted(unknown)}')
    return {'timeout': int(config['timeout'])}
