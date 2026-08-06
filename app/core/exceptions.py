class ThreeCXError(Exception):
    pass


class ThreeCXAuthenticationError(ThreeCXError):
    pass


class ThreeCXServiceUnavailableError(ThreeCXError):
    pass


class ThreeCXNotConfiguredError(ThreeCXError):
    pass
