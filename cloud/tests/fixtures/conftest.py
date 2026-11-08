"""The toy device repo is test data with its own suite; the cloud's pytest must not collect it."""

collect_ignore = ["toy_device"]
