from itertools import product

# Example arrays
array1 = ["Re", "Gi", "Na"]
array2 = ["U", "We"]

# Generate all combinations
combinations = list(product(array1, array2))

# Print all combinations
print("All combinations:")
for combo in combinations:
    print(combo)

# Or as a list of tuples
print("\nAs list:")
print(combinations)

# If you want them as concatenated strings
print("\nAs concatenated strings:")
for item1, item2 in combinations:
    print(f"{item1}{item2}")
    print(f"{item2}{item1}")
