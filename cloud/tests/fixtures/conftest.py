# The toy device is test *data*: its own suite fails on purpose (the planted D1 fault) and
# only runs inside the coder sandbox, so the cloud test run never collects it.
collect_ignore_glob = ["toy_device/*"]
