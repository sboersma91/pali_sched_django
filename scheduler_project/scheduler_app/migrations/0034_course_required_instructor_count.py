from django.core.validators import MinValueValidator
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('scheduler_app', '0033_instructorscheduleparticipation'),
    ]

    operations = [
        migrations.AddField(
            model_name='course',
            name='required_instructor_count',
            field=models.PositiveIntegerField(
                default=1,
                help_text=(
                    'Number of distinct instructors required for each occurrence '
                    'of this activity.'
                ),
                validators=[MinValueValidator(1)],
                verbose_name='Required instructor count',
            ),
        ),
    ]
