"""Errors raised before headers, or represented by response.failed after headers."""


class APIError(ValueError):
    def __init__(self, message, param=None, code="invalid_value", status=400):
        super().__init__(message)
        self.param, self.code, self.status = param, code, status

    def body(self):
        return {"error": {"message": str(self), "type": "invalid_request_error" if self.status < 500 else
                          "server_error", "param": self.param, "code": self.code}}


def unsupported(param):
    raise APIError(f"Strata does not support {param}", param, "unsupported_parameter")
