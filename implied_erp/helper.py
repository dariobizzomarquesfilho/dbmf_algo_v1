import yfinance as yf
import pandas as pd

def get_index_level(index_symbol):
    """
    Fetches the current level of the specified index using yfinance.

    Parameters:
    index_symbol (str): The symbol of the index (e.g., "^GSPC" for S&P 500).

    Returns:
    float: The current level of the index.
    """
    index = yf.Ticker(index_symbol)
    history = index.history(period="1d")
    return history['Close'].iloc[-1]
