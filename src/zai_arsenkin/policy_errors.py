class BudgetDenied(PermissionError):
    """Current server policy forbids dispatch of a previously queued paid job."""
