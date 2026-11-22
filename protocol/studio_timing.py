"""Host timing budgets for the stable one-byte-per-10-ms USB receiver."""

REQUEST_TIMEOUT_MS = 8000
STATE_VALID_MS = 2000  # Age of a correlated, complete reply on this computer.
COMMAND_QUEUE_MS = 2000
EXECUTION_TTL_MS = 2000  # Starts when the device receives the complete request.
ACCEPTED_TIMEOUT_MS = 3000
IPC_TIMEOUT_MS = 10000  # Queue (2 s), bounded USB write (7 s), scheduling (1 s).
USB_WRITE_TIMEOUT_SECONDS = 7  # A 531-byte expression takes about 5.3 s to drain.


def monotonic_ms(value):
    return type(value) is int and 0 <= value <= 9007199254740991
