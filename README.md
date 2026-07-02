# Trace Scheduling Implementation

A trace scheduling optimization pipeline for Python programs, developed as a PAR course project at ETF Belgrade.

## Overview

This project implements a complete trace scheduling compiler optimization pipeline:

1. **CFG Construction** - Builds control flow graphs from Python source code via AST parsing
2. **Branch Probability Analysis** - Assigns edge probabilities using heuristics or profile data
3. **Trace Selection** - Greedy selection of the most likely execution path
4. **List Scheduling** - Schedules operations within traces respecting data dependencies
5. **Bookkeeping** - Generates compensation code for side exits and join points
6. **Metrics** - Computes and compares optimization quality metrics

## Installation

```bash
pip install -r requirements.txt
```

For PNG visualization, install Graphviz system package:
- Windows: `choco install graphviz` or download from https://graphviz.org/download/
- Linux: `apt install graphviz`
- macOS: `brew install graphviz`

## Input/Output Specification

### Input

**Required:**
- `--source PATH` - Python source file containing:
  - `if`/`else` statements
  - `while` loops
  - `for` loops (including `range()`)
  - Variable assignments
  - `break`/`continue` statements
  - `return` statements

**Optional:**
- `--profile PATH` - JSON file overriding default branch probabilities:
  ```json
  {
    "edges": {
      "B1->B2": 0.9,
      "B1->B3": 0.1
    }
  }
  ```

### Output

The pipeline generates the following files in the output directory:

| File | Description |
|------|-------------|
| `{program}_cfg.dot` | Full CFG in Graphviz DOT format with probabilities |
| `{program}_trace.dot` | CFG with selected trace highlighted |
| `{program}_cfg.png` | PNG visualization of CFG (if Graphviz installed) |
| `{program}_trace.png` | PNG visualization with trace highlighted |
| `{program}_report.json` | Detailed scheduling report with all metrics |

### Report JSON Structure

```json
{
  "program": "examples/join_bookkeeping.py",
  "metrics": {
    "trace": ["B0", "B1", "B2", "B4"],
    "cycles": { "unoptimized": 12, "optimized": 8, "reduction_percent": 33.33 },
    "wsl": { "unoptimized": 9.6, "optimized": 6.4, "improvement_percent": 33.33 },
    "critical_path": { "before": 6, "after": 4, "reduction_percent": 33.33 },
    "bookkeeping": { "blocks_added": 1, "instructions_added": 2 },
    "code_growth": { "instructions": 2, "percent": 10.0 }
  },
  "trace": { ... },
  "schedule": { ... },
  "bookkeeping": { ... }
}
```

## Usage

### Analyze a single file

```bash
python -m trace_scheduling.main --source examples/if_dominant.py
```

### Analyze with custom profile

```bash
python -m trace_scheduling.main --source input.py --profile profile.json
```

### Run all examples

```bash
python -m trace_scheduling.main --all
```

### Legacy CFG-only mode

```bash
python PythonToCFG.py --source input.py --output output.dot
```

## Metrics

### Quality Metrics

| Metric | Definition |
|--------|------------|
| **Cycles** | Makespan (total execution time) of scheduled operations |
| **WSL (Weighted Schedule Length)** | `W(S) = Σ_j ω_j × \|S_j\|` where ω_j is path probability |
| **Critical Path** | Longest dependency chain in the schedule |

### Cost Metrics

| Metric | Definition |
|--------|------------|
| **Bookkeeping Blocks** | Number of compensation blocks added |
| **Bookkeeping Instructions** | Total compensation instructions inserted |
| **Code Growth** | Net increase in instruction count |

## Algorithm Details

### Branch Probability Heuristics

| Construct | Default Probability |
|-----------|---------------------|
| `if` True branch | 0.7 |
| `if` False branch | 0.3 |
| `for`/`while` back edge | N/(N+1) where N is trip count |
| Loop exit edge | 1/(N+1) |
| `while True` body | 0.99 |

### Trace Selection

Greedy algorithm starting from ENTRY:
1. Follow highest-probability non-back edge
2. Stop at EXIT or when no unvisited edges remain
3. Record side exits (non-taken branches) and side entrances (joins from outside trace)

### Bookkeeping

Two types of compensation code:
- **Split compensation**: When operations are speculatively executed past a branch, copy them to the non-taken path
- **Join compensation**: When operations are moved above a join point, ensure correctness on paths entering from outside the trace

## Test Examples

### (a) If-Then Dominant Branch (`examples/if_dominant.py`)
Tests basic trace selection with a high-probability True branch.

### (b) Loop with Branching (`examples/loop_branch.py`)
Tests loop probability heuristics and nested branching.

### (c) Join/Bookkeeping Test (`examples/join_bookkeeping.py`)
Tests that optimizing trace A→B→D doesn't break path A→C→D by requiring join compensation at D.

## Project Structure

```
trace_scheduling/
├── cfg/
│   ├── builder.py      # CFGBuilder, Block classes
│   └── graph.py        # CFGGraph wrapper with adjacency
├── analysis/
│   └── probabilities.py # Branch probability heuristics
├── trace/
│   └── selector.py     # Greedy trace selection
├── schedule/
│   └── list_scheduler.py # List scheduling with dependencies
├── bookkeeping/
│   └── compensation.py  # Compensation code generation
├── metrics/
│   └── evaluator.py    # Metrics computation
└── main.py             # CLI entry point

examples/
├── if_dominant.py      # Test case (a)
├── loop_branch.py      # Test case (b)
└── join_bookkeeping.py # Test case (c)
```

## References

- Fisher, J.A. (1981). "Trace Scheduling: A Technique for Global Microcode Compaction"
- Hwu, W.W. et al. (1993). "The Superblock: An Effective Technique for VLIW and Superscalar Compilation"
- Lowney, P.G. et al. (1993). "The Multiflow Trace Scheduling Compiler"

## License

GNU General Public License v3.0
