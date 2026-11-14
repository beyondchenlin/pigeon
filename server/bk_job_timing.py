"""Deadline compatibility for legacy jobs that stored a duration in count."""
EPOCH_THRESHOLD = 1_000_000_000


def deadline_timestamp(count, start_time):
    if type(count) is not int or count <= 0:
        return 0
    if count < EPOCH_THRESHOLD:
        return int(start_time) + count if type(start_time) is int and start_time > 0 else 0
    return int(count)


def deadline_reached(count, start_time, now):
    deadline = deadline_timestamp(count, start_time)
    return deadline <= 0 or now >= deadline
