def sum_positive_squares(values: list[int]) -> int:
    # graphdsl:sum_loop
    total = 0
    for item in values:
        # graphdsl:add_if_positive
        if item > 0:
            total += item * item
    # graphdsl:result
    return total
