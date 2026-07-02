# Example (c): Bookkeeping / Join test
# Tests that optimization of trace A->B->D does not break path A->C->D
#
# CFG structure:
#   A (entry + condition)
#   |
#   +-- True --> B (dominant path)
#   |            |
#   +-- False -> C (less likely path)
#                |
#                v
#                D (join point) <-- both paths merge here
#                |
#                EXIT
#
# Trace selection should pick: A -> B -> D (higher probability)
# But C -> D path must remain correct after scheduling optimization
# This requires bookkeeping (compensation code) at the join point

cond = True  # Simulates a condition that is usually True
extra = 7    # Extra variable for independent operations

if cond:           # A: Entry point with condition
    # B: Dominant path - contains INDEPENDENT operations
    b = 1
    e = extra * 2    # Independent of b
    b = b + 10
    f = e + 3        # Independent of b
    b = b * 2
else:
    # C: Less likely path operations
    c = 2
    e = extra + 1
    c = c + 5
    f = e - 1

# D: Join point - receives control from both B and C
d = b if cond else c
g = e + f            # Independent computation
d = d + 100
result = d + g
