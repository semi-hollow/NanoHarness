def normalize_config(config):
    normalized = dict(config)
    if 'legacy_timeout' in normalized and 'timeout' not in normalized:
        normalized['timeout'] = normalized.pop('legacy_timeout')
    allowed = {'timeout'}
    unknown = set(normalized) - allowed
    if unknown:
        raise ValueError(f'unknown config keys: {sorted(unknown)}')
    return {'timeout': int(normalized['timeout'])}
