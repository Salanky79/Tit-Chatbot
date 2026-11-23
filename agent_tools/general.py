"""General-purpose agent tools."""

import math

from langchain_core.tools import tool
from langchain_tavily import TavilySearch

web_search = TavilySearch(
    name="web_search",
    description=(
        "Search the internet for real-time and up-to-date information. "
        "Use this tool when the user asks about: latest news, current events, "
        "today's date or time, recent updates, current prices, stock prices, "
        "weather, sports scores, trending topics, new product releases, "
        "current software versions, who currently holds a position/role, "
        "or anything that may have changed after the model's training cutoff. "
        "Always cite that the answer is based on web search results."
    ),
    max_results=5,
    topic="general",
    search_depth="advanced",
)

@tool
def calculator(expression: str) -> str:
    """Evaluate a mathematical expression and return the numeric result.

    Use this tool when the user asks to calculate, compute, or solve a math
    problem. Input must be a valid Python math expression.

    Supported: +, -, *, /, **, //, %, math.sqrt(), math.log(), math.sin(),
    abs(), round(), min(), max(), sum().

    Examples:
        "2 + 2"            -> "4"
        "math.sqrt(144)"   -> "12.0"
        "125 * 48 / 6"     -> "1000.0"
    """
    try:
        allowed = {
            "math": math,
            "abs": abs,
            "round": round,
            "min": min,
            "max": max,
            "sum": sum,
        }
        result = eval(expression, {"__builtins__": {}}, allowed)
        return str(result)
    except Exception as e:
        return f"Calculation error: {str(e)}"
