def num_map(from_min, from_max, to_min, to_max, value):
    """convert a number within a range to a different range
    """
    from_scale = from_max - from_min
    to_scale = to_max - to_min
    value_scaled = float(value - from_min) / float(from_scale)
    return to_min + (value_scaled * to_scale)


def set_list_value(l, i, v):
    try:
        l[i] = v
    except IndexError:
        for _ in range(i-len(l)+1):
            l.append(None)
        l[i] = v