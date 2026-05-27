import sqlite3

def analyze_datapoint(node_id, new_value):
    """
    Placeholder logic. Real-time evaluation against baseline goes here.
    Returns: (is_anomaly: bool, score: int)
    """
    # Example raw check: if value is absurdly high, flag it 
    # Replace this later with a Z-score calculation from your database history
    try:
        val = float(new_value)
        if val > 1000: 
            return True, 95  # 95% anomaly score
    except:
        pass
    return False, 0