"""Exceptions for Zenbi API client."""


class ZenbiError(Exception):
    """Base exception for Zenbi API errors."""


class ZenbiAuthError(ZenbiError):
    """Raised when authentication fails or token is invalid."""


class ZenbiApiError(ZenbiError):
    """Raised when API returns an unexpected error or status code."""


class ZenbiConnectionError(ZenbiError):
    """Raised when network connection fails."""
