def sum_positive_squares(values: list[int]) -> int:
    # graphir:sum_positive_squares
    total = 0
    for item in values:
        if item > 0:
            total += item * item
    return total
