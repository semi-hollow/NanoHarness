# Webhook Contract

A successful new event returns:

```json
{"status": "accepted", "code": 200}
```

A duplicate event returns:

```json
{"status": "duplicate_ignored", "code": 200}
```

An invalid signature returns:

```json
{"status": "unauthorized", "code": 401}
```
