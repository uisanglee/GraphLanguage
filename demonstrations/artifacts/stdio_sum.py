import sys


# graphir:stdin
text = sys.stdin.read()
# graphir:solve
print(sum(int(token) for token in text.split()))
