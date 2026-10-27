class ServiceError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 422):
        super().__init__(code)
        self.code = code
        self.message = message
        self.status_code = status_code


class ProviderError(ServiceError):
    def __init__(self, code="provider_failure", status_code=502):
        super().__init__(code, "Služba extrakcie je dočasne nedostupná.", status_code)
