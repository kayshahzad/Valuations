
import numpy as np
import pandas as pd
from typing import Union, List

def dynamic_converger(current: float,
                      expected: float,
                      number_of_steps: int,
                      period_to_begin_to_converge: int) -> pd.Series:
    """
    Project values in 2 phases:
    Phase 1: Maintain 'current' value until convergence begins.
    Phase 2: Linearly converge from 'current' to 'expected' over the remaining steps.
    
    Args:
        current: Starting value.
        expected: Target value at the end of the period.
        number_of_steps: Total duration of the projection.
        period_to_begin_to_converge: The step at which convergence starts (1-based).
        
    Returns:
        pd.Series: Array of projected values of length (number_of_steps + 1) typically? 
                   Legacy code produced: (period_to_begin - 1) + (steps - period_to_begin + 1) = steps.
                   Actually legacy converger uses linspace(steps+1) which has steps+1 items?
                   Let's align strictly with legacy behavior but fixing off-by-one errors if needed.
                   Legacy:
                   array_phase1 = [current] * (period - 1)
                   array_phase2 = linspace(current, expected, steps - period + 1) (inclusive endpoints)
                   Total length = (period - 1) + (steps - period + 1) = steps.
                   
                   Example: Steps=5, Period=3.
                   Phase 1: [c, c] (Length 2)
                   Phase 2: linspace(c, e, 5 - 3 + ???). 
                   Wait, legacy passed `number_of_steps - period_to_begin_to_converge` to helper.
                   Helper did `linspace(c, e, num + 1)`.
                   So Phase 2 length = (5 - 3) + 1 = 3.
                   Total = 2 + 3 = 5. Correct.
    """
    number_of_steps = int(number_of_steps)
    period_to_begin_to_converge = int(period_to_begin_to_converge)
    
    if number_of_steps <= 0:
        return pd.Series(dtype=float)
    
    # Safety Check
    if period_to_begin_to_converge > number_of_steps:
        period_to_begin_to_converge = number_of_steps
    if period_to_begin_to_converge < 1:
        period_to_begin_to_converge = 1

    # Phase 1: Constant
    len_phase1 = period_to_begin_to_converge - 1
    array_phase1 = np.array([current] * len_phase1)

    # Phase 2: Convergence
    # Length needs to fill the rest. 
    remaining_steps = number_of_steps - len_phase1
    # Linspace generates N points. We want 'remaining_steps' points.
    # If we want to start at current and end at expected.
    # Note: linspace includes start and end. 
    # If len_phase1 > 0, the last element of phase1 is 'current'.
    # If we start phase 2 also at 'current', we repeat it?
    # Legacy: `linspace(current, expected, num + 1)` where num = steps - period.
    # Example: Steps=5, Period=3. Num = 2. Linspace(3). -> [Start, Mid, End].
    # Phase 1: [Start, Start]. Phase 2: [Start, Mid, End]. Total 5. 
    # The Transition happens at index 2 (Period 3).
    
    if remaining_steps > 0:
        array_phase2 = np.linspace(current, expected, remaining_steps)
    else:
        array_phase2 = np.array([])

    result = np.concatenate((array_phase1, array_phase2))
    return pd.Series(result)

def dynamic_converger_multiple_phase(growth_rates: List[List[float]],
                                     cycles: List[int],
                                     convergence_periods: List[int]) -> pd.Series:
    """
    Chains multiple dynamic convergence cycles.
    
    Args:
        growth_rates: List of [start, end] pairs for each cycle.
        cycles: List of lengths (years) for each cycle.
        convergence_periods: List of years before convergence starts for each cycle.
    """
    results = []
    for i in range(len(cycles)):
        res = dynamic_converger(
            current=growth_rates[i][0],
            expected=growth_rates[i][1],
            number_of_steps=cycles[i],
            period_to_begin_to_converge=convergence_periods[i]
        )
        results.append(res)
        
    return pd.concat(results, ignore_index=True)
