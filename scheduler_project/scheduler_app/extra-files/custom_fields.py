from django.db.models import TextField

'''
Currently this has been an expereinment that has not yeilded anything.... I am leaving it here as a future lesson or possibility.
'''

def parse_string():
    pass

class CommaSepField(TextField):
    
    description = "Turning a python list into a csv text field"
    
    def __init__(self, *args, **kwargs):
        self.seperater = ','
        super().__init__(*args, **kwargs)

    def get_internal_type(self):
        return "TextField"

    def get_db_prep_value(self, value, connection, prepared=False):
        value = super().get_db_prep_value(value, connection, prepared)
        print(value)
        assert(isinstance(value, str))
        if value is not None:
            return self.seperater.join([s for s in value])

    def value_to_string(self, obj):
        value = self._get_val_from_obj(obj)
        return self.get_db_prep_value(value)
    
    def from_db_value(self, value, expression, connection):
        if value is None:
            return value
        return value.split(self.seperater)

    def to_python(self, value):
        print('to_python method')
        if not value: 
            raise ValueError('There is nothing here to see')
        # if isinstance(value, THINKS THERE COULD BE AN INSTANCE OF SCHOOL HERE.???):
        #     pass

        if isinstance(value, list):
            return value
        if isinstance(value, str):
            return value.split(self.seperater)
        return "hi"

'''class CommaSepField(CharField):
    "Implements comma-separated storage of lists"

    def __init__(self, separator=",", *args, **kwargs):
        self.separator = separator
        super().__init__(*args, **kwargs)

    def deconstruct(self):
        name, path, args, kwargs = super().deconstruct()
        # Only include kwarg if it's not the default
        if self.separator != ",":
            kwargs['separator'] = self.separator
        return name, path, args, kwargs'''