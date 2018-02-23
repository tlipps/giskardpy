

class DataBus(object):
    def __init__(self):
        self._data = {}
        self.separator = '/'

    def _get_member(self, identifier,  member):
        if isinstance(identifier, dict):
            return identifier[member]
        elif isinstance(identifier, list) or (isinstance(identifier, tuple) and member.isdigit()):
            return identifier[int(member)]
        else:
            return getattr(identifier, member)

    def get_data(self, key):
        identifier_parts = key.split(self.separator)
        namespace = identifier_parts[0]
        result = self._data[namespace]
        for member in identifier_parts[1:]:
            result = self._get_member(result, member)
        return result

    def set_data(self, key, value):
        identifier_parts = key.split(self.separator)
        namespace = identifier_parts[0]
        if namespace not in self._data:
            if len(identifier_parts) > 1:
                raise KeyError('Can not access member of unknown namespace')
            else:
                self._data[namespace] = value
        else:
            result = self._data[namespace]
            for member in identifier_parts[1:-1]:
                result = self._get_member(result, member)
            if len(identifier_parts) > 1:
                member = identifier_parts[-1]
                if isinstance(result, dict):
                    result[member] = value
                elif isinstance(result, list):
                    result[int(member)] = value
                else:
                    setattr(result, member, value)
            else:
                self._data[namespace] = value




