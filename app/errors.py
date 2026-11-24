class RoastError(Exception):
    def __init__(self, code, message, status=422, payload=None):
        super().__init__(message)
        self.code, self.status, self.payload = code, status, payload
