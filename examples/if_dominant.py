# Example (a): if-then with dominant branch
# Tests trace selection with a highly probable True branch (~0.7 probability)
# Contains independent operations that can be scheduled in parallel
# Expected trace: ENTRY -> condition block -> True branch -> JOIN -> EXIT

x = 10
w = 5

if x > 0:      # Dominant branch: True (~0.7 probability heuristic)
    # These two operations are INDEPENDENT - can run in parallel
    y = x + 1
    a = w * 3
    # These depend on the above
    z = y * 2
    b = a + 1
else:          # Less likely branch (~0.3 probability)
    y = x - 1
    a = w * 2
    z = y / 2
    b = a - 1

# Final operations depend on both branches
result = z + b
