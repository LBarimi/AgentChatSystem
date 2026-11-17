class ApiError(Exception):
    def __init__(self, message, status=400, *, key=None, **values):
        super().__init__(message)
        self.status = status
        self.key = key
        self.values = values

    def response(self):
        body = {'error': str(self)}
        if self.key:
            body['error_key'] = self.key
            if self.values:
                body['error_values'] = self.values
        return body
