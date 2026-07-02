# Example (b): Loop with N iterations and internal branching
# Tests loop probability heuristics: back edge probability = N/(N+1)
# For N=10: back edge ~0.91, exit edge ~0.09
# Internal if branch tests nested probability calculation
# Contains independent operations that can be parallelized

N = 10
total = 0
even_count = 0
product = 1
alt_sum = 0

for i in range(N):      # Loop header: next edge taken N times, done edge once
    # Independent operations within loop body
    total = total + i
    product = product * 2
    
    if i % 2 == 0:      # Dominant branch: True for even i (~0.5 in this case)
        even_count = even_count + 1
        temp = i * 2
        alt_sum = alt_sum + i  # Independent
    else:
        temp = i * 3
        alt_sum = alt_sum - i

# Final computation
result = total + even_count
final = result + product
