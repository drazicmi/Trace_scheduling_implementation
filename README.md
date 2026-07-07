# Trace Scheduling Compiler Optimization Pipeline

A from-scratch implementation of trace scheduling, a classic instruction-scheduling compiler optimization originally developed for VLIW/superscalar architectures. The pipeline takes a single Python function, profiles its branch behavior, selects the most likely execution path (the "trace"), reschedules instructions along that trace for instruction-level parallelism, and inserts bookkeeping/compensation code to preserve correctness on the less-likely paths.

The project is built entirely with the Python standard library plus graphviz for visualization — no scheduling frameworks or compiler infrastructure dependencies.

## Pipeline Overview

    input.py  --->  PythonToCFG  --->  TraceSelection  --->  TraceScheduling  --->  Bookkeeping  --->  MetricsComputation
                    (build + profile      (pick hottest        (list-schedule        (insert split/         (formal
                     CFG, branch           path through          trace for ILP,       join compensation       evaluation
                     probabilities)        the CFG)              detect moved         to fix up side           report)
                                                                  instructions)        entries/exits)

Each stage is implemented as an independent module/class, orchestrated by main.py.

### 1. PythonToCFG

- Parses input.py (must contain exactly one top-level function plus a TEST_INPUTS list of exactly 100 sample inputs).
- Builds a control flow graph (CFG) of Block/Edge objects directly from the function's AST, handling if, while, for, and return.
- Instruments a copy of the AST with profiling calls, then executes the function on all 100 test inputs to record how many times each branch is actually taken.
- Converts raw counts into edge probabilities (edge.probability = edge.count / total_outgoing_count).
- Emits the CFG as a Graphviz .dot file and renders it to .png.

### 2. TraceSelection

- Implements greedy trace selection: starting at the entry block, repeatedly follows the highest-probability outgoing edge (ignoring loop-back edges) until it hits a dead end or would revisit a block.
- Marks the resulting sequence of blocks/edges as the selected trace (trace_id).
- Identifies side entries (edges from outside the trace into it) and side exits (edges leaving the trace) — these are exactly the points where bookkeeping will later be required.

### 3. TraceScheduling

- Flattens the trace's instructions into a single list and builds a data-dependency graph (RAW/WAR/WAW hazards via AST-level read/write analysis) plus a relaxed control-dependence rule: an operation only depends on a branch header if it is actually inside the region that header guards, not merely because it appears later in the trace. This is what allows genuine cross-block instruction hoisting.
- Runs a list scheduling algorithm across a configurable number of functional units (default 2), prioritizing operations by critical-path length and descendant count — the same greedy heuristic used in classical VLIW schedulers.
- Produces both a baseline schedule (naive, one instruction per cycle, single unit) and an optimized schedule, and precisely detects which instructions were moved earlier than their original program order (instruction_movements).

### 4. Bookkeeping

- For every side exit/side entry identified in step 2, computes exactly which hoisted instructions must be replayed as compensation code so that off-trace paths still observe correct program semantics.
- Synthesizes new compensation blocks (SPLIT_COMP: / JOIN_COMP: prefixed statements) and reroutes the relevant CFG edges through them.
- Reorders each trace block's visible statements to reflect the actual scheduled order, so the optimized CFG faithfully shows the effect of scheduling.
- Emits the optimized CFG (with compensation blocks highlighted in purple) as .dot/.png.

### 5. MetricsComputation

Computes a formal evaluation report comparing the baseline and optimized schedules:

Scheduling quality
- Unoptimized vs. optimized cycle counts (schedule makespan)
- Whether the critical path was reduced
- Weighted Schedule Length (WSL), defined as:

  W(S) = sum over all relevant paths j of ( Wj * |Sj| )

  where Wj is a path's probability weight and |Sj| is its scheduled length. Relevant paths are the main trace (weighted by the product of in-trace branch probabilities) plus one path per side exit actually observed during profiling (weighted by that branch's probability, with compensation instructions added to its length). Both baseline and optimized WSL are reported.

Optimization cost
- Total code size increase (real instructions added to the CFG)
- Number of added bookkeeping blocks (split vs. join compensation)
- Number of added bookkeeping instructions

## Example Output

    ==================================================================
                FORMAL TRACE SCHEDULING EVALUATION REPORT
    ==================================================================
      Trace ID: 0

    1. Scheduling Quality
    ---------------------
      Metric                                       Value
      ---------------------------------- ---------------
      Unoptimized program cycles                   11
      Optimized program cycles                     10
      Cycles reduced                                1
      Critical path status                    REDUCED

      Baseline WSL  W(S_base)                   8.154
      Optimized WSL W(S_opt)                    8.069
      WSL result                             IMPROVED

    2. Optimization Cost
    --------------------
      Metric                                       Value
      ---------------------------------- ---------------
      Original code size (instr.)                  14
      Optimized code size (instr.)                 18
      Total code size increase                      4
      Added bookkeeping blocks                      4
        - split-compensation blocks                 2
        - join-compensation blocks                  2
      Added bookkeeping instructions                4

A VERBOSE flag in main.py optionally prints full per-cycle schedules, instruction movement lists, and compensation block/edge dumps for detailed debugging.

## Project Structure

    project/
    ├── main.py                          # Orchestrates the full pipeline and prints the formal report
    ├── PythonToCFG/
    │   └── PythonToCFG.py                # AST -> CFG construction, profiling, probability computation
    ├── TraceSelection/
    │   └── TraceSelection.py             # Greedy trace selection, side entry/exit detection
    ├── TraceScheduling/
    │   └── TraceScheduling.py            # Dependency graph construction + list scheduling
    ├── Bookkeeping/
    │   └── Bookkeeping.py                # Split/join compensation block synthesis, CFG rewriting
    └── MetricsComputation/
        └── MetricsComputation.py         # WSL, cycle count, and code-size/bookkeeping cost metrics

## Usage

1. Write your target function and its 100 test inputs into PythonToCFG/input.py:

       def my_function(x, y):
           if x > y:
               return x - y
           else:
               return y - x

       TEST_INPUTS = [(1, 2), (5, 3), ...]  # exactly 100 tuples/values

2. Run the pipeline:

       python3 main.py

3. Inspect the generated artifacts:
   - PythonToCFG/cfg_output.png — the profiled, unoptimized CFG
   - TraceSelection/trace_output.png — the CFG with the selected trace highlighted
   - TraceScheduling/optimized_trace.png — the final optimized CFG with compensation blocks
   - Console output — the formal scheduling quality / optimization cost report

## Design Notes and Known Limitations

- The relaxed control-dependence rule enables genuine cross-block instruction movement, but side-effecting operations (function calls) remain full scheduling barriers, since speculating those safely would require more machinery than simple compensation blocks provide.
- WSL path weights account for probability only at the point a path diverges from the trace; nested divergence further down a side path is not separately modeled, consistent with the project's single-trace scope.
- Bookkeeping instruction cost is approximated at 1 cycle per compensation instruction, since compensation blocks are not run through the list scheduler themselves.
- Unparseable/unknown instructions are treated conservatively (assumed to read/write all variables and have a call) so the scheduler never unsafely reorders around code it cannot analyze.

## Requirements

- Python 3.9+
- [Graphviz](https://graphviz.org/) (system package) + the graphviz Python package, for .dot to .png rendering