import sys


# graphdsl:stdin
text = sys.stdin.read()
# graphdsl:parse
numbers = [int(token) for token in text.split()]
# graphdsl:sum
total = sum(numbers)
# graphdsl:format
formatted = str(total)
# graphdsl:stdout
print(formatted)
