# if I need to/want to impor the functions into the other py file, here is where that


''' The instructor model.
class Instructor(models.Model):
    fname = CharField(max_length=450)
    lname = CharField(max_length=450)
    ropes_lead = BooleanField()
    school_lead = BooleanField()
    # days_incabin = SmallIntegerField()
    # this will have to be a calculated field.
    cpr = BooleanField(choices=(("fr", "fresh"), ('hr', 'house'),('boolean field','boolean field')))
    firstaid = CharField(choices= (('yes','yes'),('jack','jack'), ('charfield','charfield')),max_length=100)
    
    def __str__(self):
        return self.fname + ' ' + self.lname'''